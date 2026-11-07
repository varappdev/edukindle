#ifndef EK_NET_H
#define EK_NET_H

#include <glib-object.h>

#include "json.h"

/*
 * Asynchronous HTTP/1.1 client for the EduKindle server.
 *
 * Each request runs a blocking GIO socket exchange on a worker thread and
 * hands the result back on the GTK main loop, so the UI never stalls while the
 * server waits on the AI. Plain HTTP only: the server is expected on the LAN.
 */

#define NET_DEFAULT_PORT 8080
#define NET_TIMEOUT_S 90 /* AI-backed calls (quizzes, explanations) can be slow */

typedef struct {
    int status;     /* HTTP status, 0 if the request never completed */
    char *body;     /* NUL-terminated response body */
    gsize body_len;
    Json *json;     /* parsed body, or NULL; callbacks may steal it */
    char *error;    /* human-readable failure, NULL on HTTP 2xx */
    double elapsed; /* seconds */
} NetResponse;

typedef void (*NetCallback)(NetResponse *resp, gpointer user_data);

/*
 * Parses "host", "host:port", "http://host:port/..." and returns a normalised
 * "host:port" (g_free it), or NULL if it isn't a usable address.
 */
char *net_normalise_server(const char *text);

/* Sets the server for subsequent requests; text as for net_normalise_server(). */
void net_init(const char *server);
const char *net_server(void);

/* Reading-text size and page width in device pixels, sent with every request
 * so the server renders display maths to match the screen. */
void net_set_display(int text_px, int page_width);

/*
 * The callback runs on the main loop, and only while `owner` (typically the
 * screen that asked) is still alive: navigating away cancels delivery.
 * server NULL means the current one; body may be NULL. The response is freed
 * after the callback returns.
 */
void net_request_full(const char *server, const char *method, const char *path,
                      const char *body, int timeout_s, GObject *owner, NetCallback cb,
                      gpointer user_data);
void net_request(const char *method, const char *path, const char *body, GObject *owner,
                 NetCallback cb, gpointer user_data);

#define net_get(path, owner, cb, data) net_request("GET", path, NULL, owner, cb, data)
#define net_post(path, body, owner, cb, data) net_request("POST", path, body, owner, cb, data)

/* URL-encodes a single path segment. */
char *net_escape(const char *segment);

/*
 * Finds EduKindle servers on the local network: tries `port` on every address
 * of this device's subnets (at most the nearest 254 per interface) at once,
 * then asks each that answers for /api/health. `found` holds "a.b.c.d:port"
 * strings and is freed after the callback; `note` explains an empty result.
 */
typedef void (*NetDiscoverFn)(GPtrArray *found, const char *note, gpointer user_data);
void net_discover(guint16 port, GObject *owner, NetDiscoverFn cb, gpointer user_data);

/* Blocking core of net_discover(), exposed for tests. hosts are IPv4
 * addresses in host byte order. Returns "a.b.c.d:port" strings (g_free each). */
GPtrArray *net_scan_hosts(const guint32 *hosts, int n, guint16 port, int timeout_ms);

#endif
