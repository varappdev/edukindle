#include "net.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <gio/gio.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define NET_MAX_BODY (8 * 1024 * 1024)
#define SCAN_CONNECT_MS 1500
#define SCAN_HEALTH_S 3
#define SCAN_BATCH 256 /* sockets open at once, well under the fd limit */

/* Current server; only touched on the main thread. Requests copy it. */
static char *server_label;
static int display_px, display_width;

typedef struct {
    char *host;
    guint16 port;
    char *label; /* "host:port", for messages and the Host header */
    char *method;
    char *path;
    char *body;
    int timeout_s;
    GObject *owner; /* weak pointer: NULLed if the owner is destroyed */
    NetCallback cb;
    gpointer user_data;
    NetResponse resp;
    gint64 started;
} Request;

/* --- addresses ---------------------------------------------------------------- */

static gboolean valid_host(const char *h)
{
    if (!h[0] || strlen(h) > 253)
        return FALSE;
    gboolean numeric = TRUE;
    for (const char *p = h; *p; p++) {
        if (!g_ascii_isalnum(*p) && *p != '.' && *p != '-')
            return FALSE;
        if (!g_ascii_isdigit(*p) && *p != '.')
            numeric = FALSE;
    }
    if (numeric) {
        struct in_addr a;
        return inet_pton(AF_INET, h, &a) == 1; /* catches 1.2.3 and 300.1.1.1 */
    }
    return TRUE;
}

char *net_normalise_server(const char *text)
{
    if (!text)
        return NULL;
    char *s = g_strstrip(g_strdup(text));
    char *p = s;
    if (g_ascii_strncasecmp(p, "http://", 7) == 0)
        p += 7;
    char *slash = strchr(p, '/');
    if (slash)
        *slash = '\0';

    long port = NET_DEFAULT_PORT;
    char *colon = strrchr(p, ':');
    if (colon) {
        *colon = '\0';
        char *end = NULL;
        port = strtol(colon + 1, &end, 10);
        if (!colon[1] || *end || port < 1 || port > 65535) {
            g_free(s);
            return NULL;
        }
    }
    char *out = valid_host(p) ? g_strdup_printf("%s:%ld", p, port) : NULL;
    g_free(s);
    return out;
}

static gboolean split_label(const char *label, char **host, guint16 *port)
{
    const char *colon = strrchr(label, ':');
    if (!colon)
        return FALSE;
    *host = g_strndup(label, colon - label);
    *port = (guint16)atoi(colon + 1);
    return TRUE;
}

void net_init(const char *server)
{
    char *label = net_normalise_server(server);
    g_free(server_label);
    server_label = label ? label : g_strdup_printf("127.0.0.1:%d", NET_DEFAULT_PORT);
}

const char *net_server(void)
{
    return server_label ? server_label : "(unset)";
}

void net_set_display(int text_px, int page_width)
{
    display_px = text_px;
    display_width = page_width;
}

char *net_escape(const char *segment)
{
    return g_uri_escape_string(segment, NULL, FALSE);
}

/* --- one HTTP exchange ----------------------------------------------------------- */

static void request_free(Request *r)
{
    g_free(r->host);
    g_free(r->label);
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

/* Blocking: connect, send, read to EOF, parse. Fills r->resp. */
static void exchange(Request *r)
{
    GError *err = NULL;
    GSocketClient *client = g_socket_client_new();
    g_socket_client_set_timeout(client, r->timeout_s);

    GSocketConnection *conn = g_socket_client_connect_to_host(client, r->host, r->port, NULL,
                                                              &err);
    if (!conn) {
        r->resp.error = g_strdup_printf("Can't reach server at %s (%s)", r->label,
                                        err ? err->message : "unknown error");
        g_clear_error(&err);
        g_object_unref(client);
        return;
    }

    GString *req = g_string_new(NULL);
    gsize body_len = r->body ? strlen(r->body) : 0;
    g_string_append_printf(req,
                           "%s %s HTTP/1.1\r\n"
                           "Host: %s\r\n"
                           "User-Agent: EduKindle/0.2\r\n"
                           "Accept: application/json\r\n"
                           "Connection: close\r\n",
                           r->method, r->path, r->label);
    if (display_px > 0)
        g_string_append_printf(req, "X-EK-Px: %d\r\nX-EK-Width: %d\r\n", display_px,
                               display_width);
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
    g_object_unref(client);
}

static Request *request_new(const char *label, const char *method, const char *path,
                            const char *body, int timeout_s)
{
    Request *r = g_new0(Request, 1);
    if (!split_label(label, &r->host, &r->port)) {
        r->host = g_strdup(label);
        r->port = NET_DEFAULT_PORT;
    }
    r->label = g_strdup(label);
    r->method = g_strdup(method);
    r->path = g_strdup(path);
    r->body = g_strdup(body);
    r->timeout_s = timeout_s;
    r->started = g_get_monotonic_time();
    return r;
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

static gpointer request_thread(gpointer data)
{
    exchange(data);
    g_idle_add(deliver, data);
    return NULL;
}

static gboolean spawn(GThreadFunc fn, gpointer data, char **error)
{
    GError *err = NULL;
#if GLIB_CHECK_VERSION(2, 32, 0)
    GThread *t = g_thread_try_new("ek-net", fn, data, &err);
    if (t)
        g_thread_unref(t);
#else
    GThread *t = g_thread_create(fn, data, FALSE, &err);
#endif
    if (!t) {
        *error = g_strdup_printf("Can't start network thread: %s", err->message);
        g_clear_error(&err);
    }
    return t != NULL;
}

void net_request_full(const char *server, const char *method, const char *path,
                      const char *body, int timeout_s, GObject *owner, NetCallback cb,
                      gpointer user_data)
{
    Request *r = request_new(server ? server : net_server(), method, path, body, timeout_s);
    r->owner = owner;
    r->cb = cb;
    r->user_data = user_data;
    g_object_add_weak_pointer(owner, (gpointer *)&r->owner);
    if (!spawn(request_thread, r, &r->resp.error))
        g_idle_add(deliver, r);
}

void net_request(const char *method, const char *path, const char *body, GObject *owner,
                 NetCallback cb, gpointer user_data)
{
    net_request_full(NULL, method, path, body, NET_TIMEOUT_S, owner, cb, user_data);
}

/* --- discovery ------------------------------------------------------------------- */

/* TCP connect to a batch of hosts at once; appends the ones that accepted. */
static void open_batch(const guint32 *hosts, int n, guint16 port, int timeout_ms, GArray *open)
{
    struct pollfd *pfd = g_new0(struct pollfd, n);
    int pending = 0;

    for (int i = 0; i < n; i++) {
        pfd[i].fd = -1;
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0)
            continue;
        fcntl(fd, F_SETFL, fcntl(fd, F_GETFL, 0) | O_NONBLOCK);
        struct sockaddr_in sa;
        memset(&sa, 0, sizeof sa);
        sa.sin_family = AF_INET;
        sa.sin_port = htons(port);
        sa.sin_addr.s_addr = htonl(hosts[i]);
        if (connect(fd, (struct sockaddr *)&sa, sizeof sa) == 0) {
            g_array_append_val(open, hosts[i]);
            close(fd);
        } else if (errno == EINPROGRESS) {
            pfd[i].fd = fd;
            pfd[i].events = POLLOUT;
            pending++;
        } else {
            close(fd);
        }
    }

    gint64 deadline = g_get_monotonic_time() + (gint64)timeout_ms * 1000;
    while (pending > 0) {
        int left = (int)((deadline - g_get_monotonic_time()) / 1000);
        if (left <= 0 || poll(pfd, n, left) <= 0)
            break;
        for (int i = 0; i < n; i++) {
            if (pfd[i].fd < 0 || !pfd[i].revents)
                continue;
            int soerr = 1;
            socklen_t len = sizeof soerr;
            if (getsockopt(pfd[i].fd, SOL_SOCKET, SO_ERROR, &soerr, &len) == 0 && soerr == 0)
                g_array_append_val(open, hosts[i]);
            close(pfd[i].fd);
            pfd[i].fd = -1;
            pending--;
        }
    }
    for (int i = 0; i < n; i++)
        if (pfd[i].fd >= 0)
            close(pfd[i].fd);
    g_free(pfd);
}

static GArray *open_hosts(const guint32 *hosts, int n, guint16 port, int timeout_ms)
{
    GArray *open = g_array_new(FALSE, FALSE, sizeof(guint32));
    for (int start = 0; start < n; start += SCAN_BATCH)
        open_batch(hosts + start, MIN(SCAN_BATCH, n - start), port, timeout_ms, open);
    return open;
}

GPtrArray *net_scan_hosts(const guint32 *hosts, int n, guint16 port, int timeout_ms)
{
    GPtrArray *found = g_ptr_array_new();
    GArray *open = open_hosts(hosts, n, port, timeout_ms);
    for (guint i = 0; i < open->len; i++) {
        struct in_addr a;
        a.s_addr = htonl(g_array_index(open, guint32, i));
        char ip[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &a, ip, sizeof ip);
        char *label = g_strdup_printf("%s:%u", ip, port);

        /* Something listens there; make sure it's EduKindle, not some other server. */
        Request *r = request_new(label, "GET", "/api/health", NULL, SCAN_HEALTH_S);
        exchange(r);
        if (!r->resp.error && json_bool(r->resp.json, "ok", FALSE))
            g_ptr_array_add(found, label);
        else
            g_free(label);
        request_free(r);
    }
    g_array_free(open, TRUE);
    return found;
}

static gboolean skip_interface(const char *name)
{
    static const char *virtual_prefixes[] = {"lo", "docker", "br-", "veth", "virbr", "tun",
                                             "tap", "wg"};
    for (gsize i = 0; i < G_N_ELEMENTS(virtual_prefixes); i++)
        if (g_str_has_prefix(name, virtual_prefixes[i]))
            return TRUE;
    return FALSE;
}

/* Addresses to try: each real interface's subnet, capped to the nearest 254. */
static GArray *local_hosts(char **note)
{
    GArray *hosts = g_array_new(FALSE, FALSE, sizeof(guint32));
    struct ifaddrs *ifs = NULL;
    if (getifaddrs(&ifs) != 0) {
        *note = g_strdup("Can't list network interfaces.");
        return hosts;
    }
    gboolean any = FALSE, capped = FALSE;
    for (struct ifaddrs *i = ifs; i; i = i->ifa_next) {
        if (!i->ifa_addr || i->ifa_addr->sa_family != AF_INET || !i->ifa_netmask ||
            !(i->ifa_flags & IFF_UP) || (i->ifa_flags & IFF_LOOPBACK) ||
            skip_interface(i->ifa_name))
            continue;
        guint32 addr = ntohl(((struct sockaddr_in *)i->ifa_addr)->sin_addr.s_addr);
        guint32 mask = ntohl(((struct sockaddr_in *)i->ifa_netmask)->sin_addr.s_addr);
        if (~mask > 0xFFu) {
            mask = 0xFFFFFF00u; /* big networks: only the /24 around us */
            capped = TRUE;
        }
        guint32 net = addr & mask, bcast = net | ~mask;
        for (guint32 h = net + 1; h < bcast; h++)
            g_array_append_val(hosts, h); /* our own address too: the server may be local */
        any = TRUE;
    }
    freeifaddrs(ifs);
    if (!any)
        *note = g_strdup("This Kindle isn't on a network: turn on Wi-Fi.");
    else if (capped)
        *note = g_strdup("Only the 254 nearest addresses of this large network were searched.");
    return hosts;
}

typedef struct {
    guint16 port;
    GObject *owner;
    NetDiscoverFn cb;
    gpointer user_data;
    GPtrArray *found;
    char *note;
} Discover;

static gboolean discover_deliver(gpointer data)
{
    Discover *d = data;
    if (d->owner) {
        g_object_remove_weak_pointer(d->owner, (gpointer *)&d->owner);
        d->cb(d->found, d->note, d->user_data);
    }
    for (guint i = 0; d->found && i < d->found->len; i++)
        g_free(g_ptr_array_index(d->found, i));
    if (d->found)
        g_ptr_array_free(d->found, TRUE);
    g_free(d->note);
    g_free(d);
    return FALSE;
}

static gpointer discover_thread(gpointer data)
{
    Discover *d = data;
    char *note = NULL;
    GArray *hosts = local_hosts(&note);
    d->found = net_scan_hosts((const guint32 *)hosts->data, (int)hosts->len, d->port,
                              SCAN_CONNECT_MS);
    if (!d->found->len && !note)
        note = g_strdup_printf("Searched %u addresses.", hosts->len);
    d->note = note;
    g_array_free(hosts, TRUE);
    g_idle_add(discover_deliver, d);
    return NULL;
}

void net_discover(guint16 port, GObject *owner, NetDiscoverFn cb, gpointer user_data)
{
    Discover *d = g_new0(Discover, 1);
    d->port = port;
    d->owner = owner;
    d->cb = cb;
    d->user_data = user_data;
    g_object_add_weak_pointer(owner, (gpointer *)&d->owner);
    if (!spawn(discover_thread, d, &d->note)) {
        d->found = g_ptr_array_new();
        g_idle_add(discover_deliver, d);
    }
}
