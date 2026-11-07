/* Host tests for json.c and net.c. Run: build/host/test_core */

#include <gio/gio.h>
#include <string.h>

#include "../src/json.h"
#include "../src/net.h"

static int failures;

#define CHECK(cond)                                                       \
    do {                                                                  \
        if (!(cond)) {                                                    \
            g_printerr("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);   \
            failures++;                                                   \
        }                                                                 \
    } while (0)

static void test_json(void)
{
    const char *src = "{\"a\": [1, 2.5, -3e2], \"s\": \"x\\\"y\\n\\u00e9\\ud83d\\ude00\","
                      " \"t\": true, \"n\": null, \"o\": {\"k\": \"v\"}, \"e\": []}";
    char *err = NULL;
    Json *j = json_parse(src, strlen(src), &err);
    CHECK(j && !err);
    CHECK(json_len(json_get(j, "a")) == 3);
    CHECK(json_at(json_get(j, "a"), 1)->num == 2.5);
    CHECK(json_at(json_get(j, "a"), 2)->num == -300);
    CHECK(!strcmp(json_str(j, "s", ""), "x\"y\n\303\251\360\237\230\200"));
    CHECK(json_bool(j, "t", FALSE));
    CHECK(json_is_null(json_get(j, "n")));
    CHECK(!strcmp(json_str(json_get(j, "o"), "k", ""), "v"));
    CHECK(json_len(json_get(j, "e")) == 0);
    CHECK(!strcmp(json_str(j, "missing", "def"), "def"));
    CHECK(json_int(NULL, "x", 7) == 7);
    json_free(j);

    const char *bad[] = {"{", "[1,]", "{\"a\" 1}", "tru", "\"abc", "1 2", "{\"a\":1}x"};
    for (gsize i = 0; i < G_N_ELEMENTS(bad); i++) {
        char *e = NULL;
        CHECK(json_parse(bad[i], strlen(bad[i]), &e) == NULL);
        CHECK(e != NULL);
        g_free(e);
    }

    GString *out = g_string_new(NULL);
    json_append_string(out, "q\"\\\n\001");
    CHECK(!strcmp(out->str, "\"q\\\"\\\\\\n\\u0001\""));
    Json *round = json_parse(out->str, out->len, NULL);
    CHECK(round && !strcmp(round->str, "q\"\\\n\001"));
    json_free(round);
    g_string_free(out, TRUE);
}

/* --- net: a one-shot fake server on a random port --------------------------- */

static const char *canned_reply;
static char *last_request;

static gboolean on_incoming(GSocketService *svc, GSocketConnection *conn, GObject *src,
                            gpointer data)
{
    (void)svc;
    (void)src;
    (void)data;
    GInputStream *in = g_io_stream_get_input_stream(G_IO_STREAM(conn));
    char buf[4096];
    gssize n = g_input_stream_read(in, buf, sizeof buf - 1, NULL, NULL);
    g_free(last_request);
    last_request = g_strndup(buf, n > 0 ? n : 0);
    GOutputStream *out = g_io_stream_get_output_stream(G_IO_STREAM(conn));
    g_output_stream_write_all(out, canned_reply, strlen(canned_reply), NULL, NULL, NULL);
    g_io_stream_close(G_IO_STREAM(conn), NULL, NULL);
    return TRUE;
}

static NetResponse got;
static GMainLoop *loop;

static void on_response(NetResponse *resp, gpointer data)
{
    (void)data;
    got.status = resp->status;
    got.error = g_strdup(resp->error);
    got.body = g_strdup(resp->body);
    got.json = resp->json; /* steal */
    resp->json = NULL;
    g_main_loop_quit(loop);
}

static void fetch(const char *reply, const char *method, const char *body)
{
    canned_reply = reply;
    memset(&got, 0, sizeof got);
    GObject *owner = g_object_new(G_TYPE_OBJECT, NULL);
    net_request(method, "/api/x", body, owner, on_response, NULL);
    g_main_loop_run(loop);
    g_object_unref(owner);
}

static void reset_got(void)
{
    g_free(got.error);
    g_free(got.body);
    json_free(got.json);
}

static void set_flag(NetResponse *resp, gpointer data)
{
    (void)resp;
    *(gboolean *)data = TRUE;
}

static void test_net(void)
{
    GSocketService *svc = g_threaded_socket_service_new(2);
    guint16 port = g_socket_listener_add_any_inet_port(G_SOCKET_LISTENER(svc), NULL, NULL);
    g_signal_connect(svc, "run", G_CALLBACK(on_incoming), NULL);
    g_socket_service_start(svc);
    char *addr = g_strdup_printf("http://127.0.0.1:%u", port);
    net_init(addr);
    g_free(addr);
    loop = g_main_loop_new(NULL, FALSE);

    fetch("HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 11\r\n\r\n"
          "{\"ok\":true}",
          "POST", "{\"a\":1}");
    CHECK(got.status == 200 && !got.error);
    CHECK(json_bool(got.json, "ok", FALSE));
    CHECK(strstr(last_request, "POST /api/x HTTP/1.1\r\n") == last_request);
    CHECK(strstr(last_request, "Content-Length: 7\r\n"));
    CHECK(g_str_has_suffix(last_request, "\r\n\r\n{\"a\":1}"));
    reset_got();

    fetch("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
          "4\r\n{\"k\"\r\n6\r\n:\"v\"}\n\r\n0\r\n\r\n",
          "GET", NULL);
    CHECK(got.status == 200 && !got.error);
    CHECK(!strcmp(json_str(got.json, "k", ""), "v"));
    reset_got();

    fetch("HTTP/1.1 404 Not Found\r\ncontent-type: application/json\r\n\r\n"
          "{\"error\":\"no section 'x'\"}",
          "GET", NULL);
    CHECK(got.status == 404);
    CHECK(got.error && strstr(got.error, "no section 'x'"));
    reset_got();

    g_socket_service_stop(svc);
    g_socket_listener_close(G_SOCKET_LISTENER(svc));
    g_object_unref(svc);

    /* Nothing listening -> transport error, status 0. */
    net_init("127.0.0.1:1");
    fetch("", "GET", NULL);
    CHECK(got.status == 0 && got.error && strstr(got.error, "Can't reach server"));
    reset_got();

    /* Owner destroyed before the reply: callback must not run. */
    net_init("127.0.0.1:1");
    GObject *owner = g_object_new(G_TYPE_OBJECT, NULL);
    gboolean called = FALSE;
    net_request("GET", "/", NULL, owner, set_flag, &called);
    g_object_unref(owner);
    GMainContext *ctx = g_main_context_default();
    for (int i = 0; i < 200; i++) {
        g_usleep(5000);
        while (g_main_context_iteration(ctx, FALSE))
            ;
    }
    CHECK(!called);
    g_main_loop_unref(loop);
}

int main(void)
{
    test_json();
    test_net();
    if (failures) {
        g_printerr("%d check(s) failed\n", failures);
        return 1;
    }
    g_print("all core tests passed\n");
    return 0;
}
