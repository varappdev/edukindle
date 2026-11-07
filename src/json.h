#ifndef EK_JSON_H
#define EK_JSON_H

#include <glib.h>

/*
 * Minimal DOM-style JSON parser. The Kindle firmware ships GLib but no JSON
 * library, and the server's responses are small, so a tree of linked nodes is
 * plenty.
 */

typedef enum {
    JSON_NULL,
    JSON_BOOL,
    JSON_NUMBER,
    JSON_STRING,
    JSON_ARRAY,
    JSON_OBJECT,
} JsonType;

typedef struct Json Json;
struct Json {
    JsonType type;
    char *key;   /* member name when this node is inside an object */
    Json *next;  /* next sibling */
    Json *child; /* first element (array) or member (object) */
    double num;
    char *str;
    gboolean boolean;
};

/* Returns NULL on error and sets *err (g_free it) if err is non-NULL. */
Json *json_parse(const char *text, gsize len, char **err);
void json_free(Json *j);

/* Lookups are NULL-safe: a missing parent yields the default. */
Json *json_get(const Json *obj, const char *key);
Json *json_at(const Json *arr, int index);
int json_len(const Json *arr);
gboolean json_is_null(const Json *j);
const char *json_str(const Json *obj, const char *key, const char *def);
double json_num(const Json *obj, const char *key, double def);
int json_int(const Json *obj, const char *key, int def);
gboolean json_bool(const Json *obj, const char *key, gboolean def);

#define json_each(item, arr) \
    for (Json *item = ((arr) ? (arr)->child : NULL); item; item = item->next)

/* Appends s as a quoted, escaped JSON string. */
void json_append_string(GString *out, const char *s);

#endif
