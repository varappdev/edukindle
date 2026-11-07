#include "net.h"

#include <gio/gio.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* LLM-backed calls (quiz generation, explanations) can take a while. */
#define NET_TIMEOUT_S 90
#define NET_MAX_BODY (8 * 1024 * 1024)

static char *server_host;
static guint16 server_port = 8080;
static char *server_label;

typedef struct {
    char *method;
    char *path;
    char *body;
    GObject *owner; /* weak pointer: NULLed if the owner is destroyed */
    NetCallback cb;
    gpointer user_data;
    NetResponse resp;
    gint64 started;
} Request;

void net_init(const char *server)
{
    const char *s = server;
    if (g_str_has_prefix(s, "http://"))
        s += 7;
    char *hostport = g_strdup(s);
    char *slash = strchr(hostport, '/');
    if (slash)
        *slash = '\0';

    g_free(server_host);
    char *colon = strrchr(hostport, ':');
    if (colon && colon[1]) {
        *colon = '\0';
        server_port = (guint16)atoi(colon + 1);
    } else {
        server_port = 8080;
    }
    server_host = g_strdup(hostport[0] ? hostport : "127.0.0.1");
    g_free(hostport);

    g_free(server_label);
    server_label = g_strdup_printf("%s:%u", server_host, server_port);
}

const char *net_server(void)
{
    return server_label ? server_label : "(unset)";
}

char *net_escape(const char *segment)
{
    return g_uri_escape_string(segment, NULL, FALSE);
}

static void request_free(Request *r)
{
    g_free(r->method);
    g_free(r->path);
    g_free(r->body);
    g_free(r->resp.body);
    g_free(r->resp.error);
    json_free(r->resp.json);
    g_free(r);
}

/* Decodes a chunked transfer-encoded body in place. */
static gsize dechunk(char *buf, gsize len)
{
    char *in = buf, *out = buf, *end = buf + len;
    while (in < end) {
        char *eol = g_strstr_len(in, end - in, "\r\n");
        if (!eol)
            break;
        gsize n = strtoul(in, NULL, 16);
        in = eol + 2;
        if (n == 0 || n > (gsize)(end - in))
            break;
        memmove(out, in, n);
        out += n;
        in += n + 2; /* chunk data + CRLF */
    }
    return out - buf;
}

static void parse_response(Request *r, char *raw, gsize len)
{
    NetResponse *resp = &r->resp;
    char *hdr_end = g_strstr_len(raw, len, "\r\n\r\n");
    if (!hdr_end || sscanf(raw, "HTTP/%*d.%*d %d", &resp->status) != 1) {
        resp->status = 0;
        resp->error = g_strdup("Malformed reply from server");
        return;
    }
    *hdr_end = '\0';
    char *headers = g_ascii_strdown(raw, -1);
    char *body = hdr_end + 4;
    gsize body_len = len - (body - raw);

    if (strstr(headers, "\r\ntransfer-encoding: chunked"))
        body_len = dechunk(body, body_len);
    char *cl = strstr(headers, "\r\ncontent-length:");
    if (cl) {
        gsize n = strtoul(cl + 17, NULL, 10);
        if (n < body_len)
            body_len = n;
    }
    gboolean is_json = strstr(headers, "\r\ncontent-type: application/json") != NULL;
    g_free(headers);

    resp->body = g_strndup(body, body_len);
    resp->body_len = body_len;
    if (is_json)
        resp->json = json_parse(resp->body, body_len, NULL);

    if (resp->status < 200 || resp->status >= 300) {
        const char *msg = json_str(resp->json, "error", NULL);
        if (!msg)
            msg = json_str(resp->json, "detail", NULL);
        resp->error = g_strdup_printf("Server error %d%s%s", resp->status, msg ? ": " : "",
                                      msg ? msg : "");
    } else if (is_json && !resp->json) {
        resp->error = g_strdup("Server sent invalid JSON");
    }
}

static gboolean deliver(gpointer data)
{
    Request *r = data;
    r->resp.elapsed = (g_get_monotonic_time() - r->started) / 1e6;
    if (r->owner) {
        g_object_remove_weak_pointer(r->owner, (gpointer *)&r->owner);
        if (r->resp.error)
            g_warning("%s %s: %s", r->method, r->path, r->resp.error);
        r->cb(&r->resp, r->user_data);
    }
    request_free(r);
    return FALSE;
}

static gpointer worker(gpointer data)
{
    Request *r = data;
    GError *err = NULL;
    GSocketClient *client = g_socket_client_new();
    g_socket_client_set_timeout(client, NET_TIMEOUT_S);

    GSocketConnection *conn =
        g_socket_client_connect_to_host(client, server_host, server_port, NULL, &err);
    if (!conn) {
        r->resp.error = g_strdup_printf("Can't reach server at %s (%s)", server_label,
                                        err ? err->message : "unknown error");
        g_clear_error(&err);
        goto done;
    }

    GString *req = g_string_new(NULL);
    gsize body_len = r->body ? strlen(r->body) : 0;
    g_string_append_printf(req,
                           "%s %s HTTP/1.1\r\n"
                           "Host: %s\r\n"
                           "User-Agent: EduKindle/0.1\r\n"
                           "Accept: application/json\r\n"
                           "Connection: close\r\n",
                           r->method, r->path, server_label);
    if (r->body)
        g_string_append_printf(req,
                               "Content-Type: application/json\r\n"
                               "Content-Length: %lu\r\n",
                               (unsigned long)body_len);
    g_string_append(req, "\r\n");
    if (r->body)
        g_string_append_len(req, r->body, body_len);

    GOutputStream *out = g_io_stream_get_output_stream(G_IO_STREAM(conn));
    GInputStream *in = g_io_stream_get_input_stream(G_IO_STREAM(conn));
    if (!g_output_stream_write_all(out, req->str, req->len, NULL, NULL, &err)) {
        r->resp.error = g_strdup_printf("Send failed: %s", err->message);
        g_clear_error(&err);
    } else {
        GByteArray *buf = g_byte_array_new();
        guint8 chunk[16384];
        for (;;) {
            gssize n = g_input_stream_read(in, chunk, sizeof chunk, NULL, &err);
            if (n <= 0)
                break;
            g_byte_array_append(buf, chunk, (guint)n);
            if (buf->len > NET_MAX_BODY)
                break;
        }
        if (err) {
            r->resp.error = g_strdup_printf("Receive failed: %s", err->message);
            g_clear_error(&err);
        } else {
            g_byte_array_append(buf, (const guint8 *)"", 1); /* NUL for sscanf */
            parse_response(r, (char *)buf->data, buf->len - 1);
        }
        g_byte_array_free(buf, TRUE);
    }
    g_string_free(req, TRUE);
    g_io_stream_close(G_IO_STREAM(conn), NULL, NULL);
    g_object_unref(conn);

done:
    g_object_unref(client);
    g_idle_add(deliver, r);
    return NULL;
}

void net_request(const char *method, const char *path, const char *body, GObject *owner,
                 NetCallback cb, gpointer user_data)
{
    Request *r = g_new0(Request, 1);
    r->method = g_strdup(method);
    r->path = g_strdup(path);
    r->body = g_strdup(body);
    r->owner = owner;
    r->cb = cb;
    r->user_data = user_data;
    r->started = g_get_monotonic_time();
    g_object_add_weak_pointer(owner, (gpointer *)&r->owner);

    GError *err = NULL;
#if GLIB_CHECK_VERSION(2, 32, 0)
    GThread *t = g_thread_try_new("ek-net", worker, r, &err);
    if (t)
        g_thread_unref(t);
#else
    GThread *t = g_thread_create(worker, r, FALSE, &err);
#endif
    if (!t) {
        r->resp.error = g_strdup_printf("Can't start network thread: %s", err->message);
        g_clear_error(&err);
        g_idle_add(deliver, r);
    }
}
