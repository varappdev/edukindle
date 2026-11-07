#include "json.h"

#include <stdlib.h>
#include <string.h>

#define MAX_DEPTH 64

typedef struct {
    const char *p;
    const char *end;
    const char *err;
    int depth;
} Parser;

static Json *parse_value(Parser *ps);

static void skip_ws(Parser *ps)
{
    while (ps->p < ps->end &&
           (*ps->p == ' ' || *ps->p == '\t' || *ps->p == '\n' || *ps->p == '\r'))
        ps->p++;
}

static gboolean expect_word(Parser *ps, const char *word)
{
    gsize n = strlen(word);
    if ((gsize)(ps->end - ps->p) < n || memcmp(ps->p, word, n) != 0) {
        ps->err = "invalid literal";
        return FALSE;
    }
    ps->p += n;
    return TRUE;
}

static int hex4(const char *s)
{
    int v = 0;
    for (int i = 0; i < 4; i++) {
        int d = g_ascii_xdigit_value(s[i]);
        if (d < 0)
            return -1;
        v = v * 16 + d;
    }
    return v;
}

static char *parse_string_raw(Parser *ps)
{
    if (ps->p >= ps->end || *ps->p != '"') {
        ps->err = "expected string";
        return NULL;
    }
    ps->p++;
    GString *out = g_string_new(NULL);
    while (ps->p < ps->end && *ps->p != '"') {
        char c = *ps->p++;
        if (c != '\\') {
            g_string_append_c(out, c);
            continue;
        }
        if (ps->p >= ps->end)
            break;
        c = *ps->p++;
        switch (c) {
        case '"': g_string_append_c(out, '"'); break;
        case '\\': g_string_append_c(out, '\\'); break;
        case '/': g_string_append_c(out, '/'); break;
        case 'b': g_string_append_c(out, '\b'); break;
        case 'f': g_string_append_c(out, '\f'); break;
        case 'n': g_string_append_c(out, '\n'); break;
        case 'r': g_string_append_c(out, '\r'); break;
        case 't': g_string_append_c(out, '\t'); break;
        case 'u': {
            if (ps->end - ps->p < 4 || hex4(ps->p) < 0) {
                ps->err = "bad \\u escape";
                g_string_free(out, TRUE);
                return NULL;
            }
            gunichar cp = (gunichar)hex4(ps->p);
            ps->p += 4;
            /* Surrogate pair: 😀 */
            if (cp >= 0xD800 && cp <= 0xDBFF && ps->end - ps->p >= 6 &&
                ps->p[0] == '\\' && ps->p[1] == 'u' && hex4(ps->p + 2) >= 0xDC00 &&
                hex4(ps->p + 2) <= 0xDFFF) {
                cp = 0x10000 + ((cp - 0xD800) << 10) + ((gunichar)hex4(ps->p + 2) - 0xDC00);
                ps->p += 6;
            }
            if (cp >= 0xD800 && cp <= 0xDFFF)
                cp = 0xFFFD;
            g_string_append_unichar(out, cp);
            break;
        }
        default:
            ps->err = "bad escape";
            g_string_free(out, TRUE);
            return NULL;
        }
    }
    if (ps->p >= ps->end) {
        ps->err = "unterminated string";
        g_string_free(out, TRUE);
        return NULL;
    }
    ps->p++; /* closing quote */
    return g_string_free(out, FALSE);
}

static Json *new_node(JsonType type)
{
    Json *j = g_new0(Json, 1);
    j->type = type;
    return j;
}

static Json *parse_container(Parser *ps, gboolean is_object)
{
    char close = is_object ? '}' : ']';
    Json *node = new_node(is_object ? JSON_OBJECT : JSON_ARRAY);
    Json **tail = &node->child;

    if (++ps->depth > MAX_DEPTH) {
        ps->err = "nesting too deep";
        json_free(node);
        return NULL;
    }
    ps->p++; /* opening bracket */
    skip_ws(ps);
    if (ps->p < ps->end && *ps->p == close) {
        ps->p++;
        ps->depth--;
        return node;
    }
    for (;;) {
        char *key = NULL;
        skip_ws(ps);
        if (is_object) {
            key = parse_string_raw(ps);
            if (!key)
                goto fail;
            skip_ws(ps);
            if (ps->p >= ps->end || *ps->p != ':') {
                g_free(key);
                ps->err = "expected ':'";
                goto fail;
            }
            ps->p++;
        }
        Json *val = parse_value(ps);
        if (!val) {
            g_free(key);
            goto fail;
        }
        val->key = key;
        *tail = val;
        tail = &val->next;

        skip_ws(ps);
        if (ps->p < ps->end && *ps->p == ',') {
            ps->p++;
            continue;
        }
        if (ps->p < ps->end && *ps->p == close) {
            ps->p++;
            ps->depth--;
            return node;
        }
        ps->err = is_object ? "expected ',' or '}'" : "expected ',' or ']'";
        goto fail;
    }
fail:
    json_free(node);
    return NULL;
}

static Json *parse_value(Parser *ps)
{
    skip_ws(ps);
    if (ps->p >= ps->end) {
        ps->err = "unexpected end of input";
        return NULL;
    }
    switch (*ps->p) {
    case '{': return parse_container(ps, TRUE);
    case '[': return parse_container(ps, FALSE);
    case '"': {
        char *s = parse_string_raw(ps);
        if (!s)
            return NULL;
        Json *j = new_node(JSON_STRING);
        j->str = s;
        return j;
    }
    case 't':
    case 'f': {
        gboolean v = *ps->p == 't';
        if (!expect_word(ps, v ? "true" : "false"))
            return NULL;
        Json *j = new_node(JSON_BOOL);
        j->boolean = v;
        return j;
    }
    case 'n':
        if (!expect_word(ps, "null"))
            return NULL;
        return new_node(JSON_NULL);
    default: {
        /* g_ascii_strtod needs a NUL-terminated buffer; copy the token. */
        const char *start = ps->p;
        while (ps->p < ps->end && strchr("+-0123456789.eE", *ps->p))
            ps->p++;
        if (ps->p == start) {
            ps->err = "unexpected character";
            return NULL;
        }
        char *tok = g_strndup(start, ps->p - start);
        char *endp = NULL;
        double v = g_ascii_strtod(tok, &endp);
        gboolean ok = endp && *endp == '\0';
        g_free(tok);
        if (!ok) {
            ps->err = "bad number";
            return NULL;
        }
        Json *j = new_node(JSON_NUMBER);
        j->num = v;
        return j;
    }
    }
}

Json *json_parse(const char *text, gsize len, char **err)
{
    Parser ps = {text, text + len, NULL, 0};
    Json *root = parse_value(&ps);
    if (root) {
        skip_ws(&ps);
        if (ps.p != ps.end) {
            ps.err = "trailing characters";
            json_free(root);
            root = NULL;
        }
    }
    if (!root && err)
        *err = g_strdup_printf("JSON error at byte %ld: %s", (long)(ps.p - text),
                               ps.err ? ps.err : "?");
    return root;
}

void json_free(Json *j)
{
    while (j) {
        Json *next = j->next;
        json_free(j->child);
        g_free(j->key);
        g_free(j->str);
        g_free(j);
        j = next;
    }
}

Json *json_get(const Json *obj, const char *key)
{
    if (!obj || obj->type != JSON_OBJECT)
        return NULL;
    for (Json *m = obj->child; m; m = m->next)
        if (m->key && strcmp(m->key, key) == 0)
            return m;
    return NULL;
}

Json *json_at(const Json *arr, int index)
{
    if (!arr || arr->type != JSON_ARRAY || index < 0)
        return NULL;
    Json *e = arr->child;
    while (e && index--)
        e = e->next;
    return e;
}

int json_len(const Json *arr)
{
    int n = 0;
    if (arr && (arr->type == JSON_ARRAY || arr->type == JSON_OBJECT))
        for (Json *e = arr->child; e; e = e->next)
            n++;
    return n;
}

gboolean json_is_null(const Json *j)
{
    return !j || j->type == JSON_NULL;
}

const char *json_str(const Json *obj, const char *key, const char *def)
{
    Json *v = json_get(obj, key);
    return v && v->type == JSON_STRING ? v->str : def;
}

double json_num(const Json *obj, const char *key, double def)
{
    Json *v = json_get(obj, key);
    return v && v->type == JSON_NUMBER ? v->num : def;
}

int json_int(const Json *obj, const char *key, int def)
{
    Json *v = json_get(obj, key);
    return v && v->type == JSON_NUMBER ? (int)v->num : def;
}

gboolean json_bool(const Json *obj, const char *key, gboolean def)
{
    Json *v = json_get(obj, key);
    return v && v->type == JSON_BOOL ? v->boolean : def;
}

void json_append_string(GString *out, const char *s)
{
    g_string_append_c(out, '"');
    for (const unsigned char *p = (const unsigned char *)s; p && *p; p++) {
        switch (*p) {
        case '"': g_string_append(out, "\\\""); break;
        case '\\': g_string_append(out, "\\\\"); break;
        case '\n': g_string_append(out, "\\n"); break;
        case '\r': g_string_append(out, "\\r"); break;
        case '\t': g_string_append(out, "\\t"); break;
        default:
            if (*p < 0x20)
                g_string_append_printf(out, "\\u%04x", *p);
            else
                g_string_append_c(out, (char)*p);
        }
    }
    g_string_append_c(out, '"');
}
