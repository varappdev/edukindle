#ifndef EK_NET_H
#define EK_NET_H

#include <glib-object.h>

#include "json.h"

/*
 * Asynchronous HTTP/1.1 client for the EduKindle server.
 *
 * Each request runs a blocking GIO socket exchange on a worker thread and
 * hands the result back on the GTK main loop, so the UI never stalls while the
 * server waits on OpenAI. Plain HTTP only: the server is expected on the LAN.
 */

typedef struct {
    int status;     /* HTTP status, 0 if the request never completed */
    char *body;     /* NUL-terminated response body */
    gsize body_len;
    Json *json;     /* parsed body, or NULL; callbacks may steal it */
    char *error;    /* human-readable failure, NULL on HTTP 2xx */
    double elapsed; /* seconds */
} NetResponse;

typedef void (*NetCallback)(NetResponse *resp, gpointer user_data);

/* "host:port" or "http://host:port". */
void net_init(const char *server);
const char *net_server(void);

/*
 * The callback runs on the main loop, and only while `owner` (typically the
 * screen that asked) is still alive: navigating away cancels delivery.
 * body may be NULL. The response is freed after the callback returns.
 */
void net_request(const char *method, const char *path, const char *body, GObject *owner,
                 NetCallback cb, gpointer user_data);

#define net_get(path, owner, cb, data) net_request("GET", path, NULL, owner, cb, data)
#define net_post(path, body, owner, cb, data) net_request("POST", path, body, owner, cb, data)

/* URL-encodes a single path segment. */
char *net_escape(const char *segment);

#endif
