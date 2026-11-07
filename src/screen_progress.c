/* Mastery overview and the knowledge-graph map, both fed by the Curriculum
 * Architect. */

#include <math.h>
#include <string.h>

#include "app.h"

static void go_back(GtkWidget *b, gpointer data)
{
    (void)b;
    (void)data;
    app_pop();
}

static const char *status_word(const char *status)
{
    if (!g_strcmp0(status, "mastered"))
        return "Mastered";
    if (!g_strcmp0(status, "learning"))
        return "Learning";
    if (!g_strcmp0(status, "weak"))
        return "Weak";
    return "Not tested";
}

static int pct(double v)
{
    return (int)(v * 100 + 0.5);
}

/* --- Progress ---------------------------------------------------------------------- */

typedef struct {
    GtkWidget *screen;
    char *doc_id;
    GtkWidget *stats;
    GtkWidget *page_label;
    GtkWidget *body;
} Progress;

static void progress_free(gpointer data)
{
    Progress *p = data;
    g_free(p->doc_id);
    g_free(p);
}

static GtkWidget *stat_box(const char *value, const char *caption)
{
    GtkWidget *v = gtk_vbox_new(FALSE, ui_px(4));
    GtkWidget *big = ui_label(value, UI_FONT_UI, 64, TRUE);
    GtkWidget *small = ui_label(caption, UI_FONT_UI, UI_SMALL_PX - 2, FALSE);
    gtk_misc_set_alignment(GTK_MISC(big), 0.5, 0.5);
    gtk_misc_set_alignment(GTK_MISC(small), 0.5, 0.5);
    gtk_box_pack_start(GTK_BOX(v), big, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(v), small, FALSE, FALSE, 0);
    return ui_frame(v, 3, FALSE);
}

static void progress_page_changed(Pager *pg, gpointer data)
{
    Progress *p = data;
    char *s = g_strdup_printf("%d/%d", pager_page(pg) + 1, pager_pages(pg));
    gtk_label_set_text(GTK_LABEL(p->page_label), s);
    g_free(s);
}

static void progress_map(GtkWidget *b, gpointer data)
{
    (void)b;
    Progress *p = data;
    app_push(screen_map_new(p->doc_id));
}

static void progress_loaded(NetResponse *resp, gpointer data)
{
    Progress *p = data;
    ui_clear(p->body);
    ui_clear(p->stats);
    if (resp->error) {
        char *m = g_markup_printf_escaped("<b>Couldn't load progress.</b>\n\n%s", resp->error);
        gtk_box_pack_start(GTK_BOX(p->body), ui_message(m), TRUE, TRUE, 0);
        g_free(m);
        gtk_widget_show_all(p->body);
        return;
    }
    Json *j = resp->json;
    char *a = g_strdup_printf("%d%%", pct(json_num(j, "overall", 0)));
    char *b = g_strdup_printf("%d/%d", json_int(j, "sections_passed", 0),
                              json_int(j, "sections_total", 0));
    char *c = g_strdup_printf("%d", json_int(json_get(j, "counts"), "weak", 0));
    gtk_box_pack_start(GTK_BOX(p->stats), stat_box(a, "mastery"), TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(p->stats), stat_box(b, "sections passed"), TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(p->stats), stat_box(c, "weak concepts"), TRUE, TRUE, 0);
    g_free(a);
    g_free(b);
    g_free(c);

    GtkWidget *area = pager_new(UI_FONT_UI, UI_SMALL_PX + 4);
    Pager *pg = pager_of(area);
    pager_on_change(pg, progress_page_changed, p);
    Json *doc = json_get(j, "doc"); /* coach note, gaps, concepts: composed by the server */
    if (doc)
        pager_set_doc(pg, doc);
    else
        pager_set_text(pg, APP_OLD_SERVER);
    app_set_pager(p->screen, pg);
    gtk_box_pack_start(GTK_BOX(p->body), area, TRUE, TRUE, 0);
    gtk_widget_show_all(p->stats);
    gtk_widget_show_all(p->body);
}

static void progress_load(gpointer data)
{
    Progress *p = data;
    char *esc = net_escape(p->doc_id);
    char *path = g_strdup_printf("/api/progress/%s", esc);
    net_get(path, G_OBJECT(p->screen), progress_loaded, p);
    g_free(path);
    g_free(esc);
}

GtkWidget *screen_progress_new(const char *doc_id)
{
    Progress *p = g_new0(Progress, 1);
    p->doc_id = g_strdup(doc_id);
    p->screen = ui_screen();
    g_object_set_data_full(G_OBJECT(p->screen), "ek-state", p, progress_free);

    GtkWidget *right;
    gtk_box_pack_start(GTK_BOX(p->screen), ui_header("Progress", go_back, NULL, NULL, &right),
                       FALSE, FALSE, 0);
    p->page_label = ui_label("", UI_FONT_UI, UI_SMALL_PX, TRUE);
    gtk_box_pack_end(GTK_BOX(right), p->page_label, FALSE, FALSE, 0);

    p->stats = ui_hbox_equal();
    gtk_box_pack_start(GTK_BOX(p->screen), p->stats, FALSE, FALSE, 0);
    p->body = gtk_vbox_new(FALSE, 0);
    gtk_box_pack_start(GTK_BOX(p->body), ui_message("Loading\342\200\246"), TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(p->screen), p->body, TRUE, TRUE, 0);
    gtk_box_pack_end(GTK_BOX(p->screen),
                     ui_button("Knowledge map", UI_BTN_PRIMARY, progress_map, p), FALSE, FALSE, 0);

    app_on_resume(p->screen, progress_load, p);
    progress_load(p);
    return p->screen;
}

/* --- Knowledge map ------------------------------------------------------------------- */

typedef struct {
    char *id, *label, *description, *status, *section;
    double mastery, x, y;
    double bx, by, bw, bh; /* box on screen, computed while drawing */
} Node;

typedef struct {
    int from, to;
} Edge;

typedef struct {
    GtkWidget *screen;
    char *doc_id;
    GtkWidget *area;
    GtkWidget *info;
    GtkWidget *explain_btn;
    GtkWidget *open_btn;
    GArray *nodes;
    GArray *edges;
    int selected;
    char *message; /* shown instead of the graph while loading or empty */
} Map;

static void map_free(gpointer data)
{
    Map *m = data;
    for (guint i = 0; i < m->nodes->len; i++) {
        Node *n = &g_array_index(m->nodes, Node, i);
        g_free(n->id);
        g_free(n->label);
        g_free(n->description);
        g_free(n->status);
        g_free(n->section);
    }
    g_array_free(m->nodes, TRUE);
    g_array_free(m->edges, TRUE);
    g_free(m->doc_id);
    g_free(m->message);
    g_free(m);
}

static int map_find(Map *m, const char *id)
{
    for (guint i = 0; i < m->nodes->len; i++)
        if (!g_strcmp0(g_array_index(m->nodes, Node, i).id, id))
            return (int)i;
    return -1;
}

static void rounded_rect(cairo_t *cr, double x, double y, double w, double h, double r)
{
    cairo_new_sub_path(cr);
    cairo_arc(cr, x + w - r, y + r, r, -G_PI / 2, 0);
    cairo_arc(cr, x + w - r, y + h - r, r, 0, G_PI / 2);
    cairo_arc(cr, x + r, y + h - r, r, G_PI / 2, G_PI);
    cairo_arc(cr, x + r, y + r, r, G_PI, 3 * G_PI / 2);
    cairo_close_path(cr);
}

/* Node outline encodes status in pure black and white: solid fill = mastered,
 * heavy border = learning, dashed = weak, dotted = not tested. */
static void style_outline(cairo_t *cr, const char *status)
{
    double dashed[] = {ui_px(14), ui_px(8)};
    double dotted[] = {ui_px(3), ui_px(7)};
    cairo_set_dash(cr, NULL, 0, 0);
    if (!g_strcmp0(status, "weak")) {
        cairo_set_line_width(cr, ui_px(5));
        cairo_set_dash(cr, dashed, 2, 0);
    } else if (!g_strcmp0(status, "unknown")) {
        cairo_set_line_width(cr, ui_px(3));
        cairo_set_dash(cr, dotted, 2, 0);
    } else {
        cairo_set_line_width(cr, ui_px(4));
    }
}

static void draw_legend(cairo_t *cr, PangoLayout *pl, double x, double y, double w)
{
    static const char *statuses[] = {"mastered", "learning", "weak", "unknown"};
    double sw = ui_px(44), sh = ui_px(26), slot = w / 4;
    for (int i = 0; i < 4; i++) {
        double sx = x + i * slot;
        rounded_rect(cr, sx, y, sw, sh, ui_px(5));
        if (i == 0) {
            cairo_fill_preserve(cr);
        }
        style_outline(cr, statuses[i]);
        cairo_stroke(cr);
        cairo_set_dash(cr, NULL, 0, 0);
        pango_layout_set_width(pl, (int)(slot - sw - ui_px(12)) * PANGO_SCALE);
        pango_layout_set_text(pl, status_word(statuses[i]), -1);
        int tw, th;
        pango_layout_get_pixel_size(pl, &tw, &th);
        cairo_move_to(cr, sx + sw + ui_px(10), y + (sh - th) / 2.0);
        pango_cairo_show_layout(cr, pl);
    }
}

static gboolean map_expose(GtkWidget *w, GdkEventExpose *ev, gpointer data)
{
    Map *m = data;
    GtkAllocation a;
    gtk_widget_get_allocation(w, &a);
    cairo_t *cr = gdk_cairo_create(gtk_widget_get_window(w));
    gdk_cairo_region(cr, ev->region);
    cairo_clip(cr);
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_paint(cr);
    cairo_set_source_rgb(cr, 0, 0, 0);

    PangoLayout *pl = gtk_widget_create_pango_layout(w, NULL);
    if (m->message || m->nodes->len == 0) {
        PangoFontDescription *fd = ui_font(UI_FONT_UI, UI_TEXT_PX, FALSE);
        pango_layout_set_font_description(pl, fd);
        pango_font_description_free(fd);
        pango_layout_set_width(pl, a.width * PANGO_SCALE);
        pango_layout_set_alignment(pl, PANGO_ALIGN_CENTER);
        pango_layout_set_wrap(pl, PANGO_WRAP_WORD);
        pango_layout_set_text(pl, m->message ? m->message : "No concepts mapped yet.", -1);
        int tw, th;
        pango_layout_get_pixel_size(pl, &tw, &th);
        cairo_move_to(cr, 0, (a.height - th) / 2.0);
        pango_cairo_show_layout(cr, pl);
        g_object_unref(pl);
        cairo_destroy(cr);
        return TRUE;
    }

    double legend_h = ui_px(60);
    double gh = a.height - legend_h - ui_px(10);

    /* Count rows and the widest row to size the boxes. */
    GHashTable *per_row = g_hash_table_new(g_double_hash, g_double_equal);
    GArray *row_keys = g_array_new(FALSE, FALSE, sizeof(double));
    int max_cols = 1;
    for (guint i = 0; i < m->nodes->len; i++) {
        Node *n = &g_array_index(m->nodes, Node, i);
        int c = GPOINTER_TO_INT(g_hash_table_lookup(per_row, &n->y));
        if (c == 0)
            g_array_append_val(row_keys, n->y);
        c++;
        g_hash_table_replace(per_row, &n->y, GINT_TO_POINTER(c));
        max_cols = MAX(max_cols, c);
    }
    int rows = MAX((int)row_keys->len, 1);
    g_hash_table_destroy(per_row);
    g_array_free(row_keys, TRUE);

    double gap = ui_px(18);
    double bw = MIN(a.width / max_cols - gap, ui_px(320));
    double bh = MIN(gh / rows - ui_px(30), ui_px(140));
    double font_px = MIN(26.0, 26.0 * bw / ui_px(240));
    font_px = MAX(font_px, 18.0);

    for (guint i = 0; i < m->nodes->len; i++) {
        Node *n = &g_array_index(m->nodes, Node, i);
        n->bw = bw;
        n->bh = bh;
        n->bx = n->x * a.width - bw / 2;
        n->by = n->y * gh - bh / 2 + ui_px(4);
    }

    /* Edges: prerequisite (above) -> concept it unlocks (below). */
    cairo_set_line_width(cr, ui_px(3));
    for (guint i = 0; i < m->edges->len; i++) {
        Edge *e = &g_array_index(m->edges, Edge, i);
        Node *s = &g_array_index(m->nodes, Node, e->from);
        Node *t = &g_array_index(m->nodes, Node, e->to);
        double x1 = s->bx + bw / 2, y1 = s->by + bh;
        double x2 = t->bx + bw / 2, y2 = t->by - ui_px(2);
        cairo_move_to(cr, x1, y1);
        cairo_line_to(cr, x2, y2);
        cairo_stroke(cr);
        double ang = atan2(y2 - y1, x2 - x1), al = ui_px(20), aw = 0.42;
        cairo_move_to(cr, x2, y2);
        cairo_line_to(cr, x2 - al * cos(ang - aw), y2 - al * sin(ang - aw));
        cairo_line_to(cr, x2 - al * cos(ang + aw), y2 - al * sin(ang + aw));
        cairo_close_path(cr);
        cairo_fill(cr);
    }

    PangoFontDescription *fd = ui_font(UI_FONT_UI, font_px, TRUE);
    pango_layout_set_font_description(pl, fd);
    pango_font_description_free(fd);
    pango_layout_set_alignment(pl, PANGO_ALIGN_CENTER);
    pango_layout_set_wrap(pl, PANGO_WRAP_WORD);
    pango_layout_set_ellipsize(pl, PANGO_ELLIPSIZE_END);
    double pad = ui_px(9), bar_h = ui_px(10), r = ui_px(10);
    /* Labels get whatever height the box leaves above the mastery bar. */
    pango_layout_set_height(pl, (int)(bh - bar_h - 2.2 * pad) * PANGO_SCALE);
    for (guint i = 0; i < m->nodes->len; i++) {
        Node *n = &g_array_index(m->nodes, Node, i);
        gboolean dark = !g_strcmp0(n->status, "mastered");

        if ((int)i == m->selected) {
            /* Selection: a second frame around the node. */
            cairo_set_dash(cr, NULL, 0, 0);
            cairo_set_line_width(cr, ui_px(4));
            rounded_rect(cr, n->bx - ui_px(9), n->by - ui_px(9), bw + ui_px(18), bh + ui_px(18),
                         r + ui_px(6));
            cairo_stroke(cr);
        }
        rounded_rect(cr, n->bx, n->by, bw, bh, r);
        cairo_set_source_rgb(cr, dark ? 0 : 1, dark ? 0 : 1, dark ? 0 : 1);
        cairo_fill_preserve(cr);
        cairo_set_source_rgb(cr, 0, 0, 0);
        style_outline(cr, n->status);
        cairo_stroke(cr);
        cairo_set_dash(cr, NULL, 0, 0);

        double ink = dark ? 1 : 0;
        cairo_set_source_rgb(cr, ink, ink, ink);
        pango_layout_set_width(pl, (int)(bw - 2 * pad) * PANGO_SCALE);
        pango_layout_set_text(pl, n->label, -1);
        int tw, th;
        pango_layout_get_pixel_size(pl, &tw, &th);
        double text_area = bh - bar_h - 2 * pad;
        cairo_move_to(cr, n->bx + pad, n->by + pad + MAX(0, (text_area - th) / 2.0));
        pango_cairo_show_layout(cr, pl);

        /* Mastery bar: outline plus flat fill. */
        double bx = n->bx + pad * 1.5, by = n->by + bh - pad - bar_h, bwid = bw - 3 * pad;
        cairo_set_line_width(cr, ui_px(2));
        cairo_rectangle(cr, bx, by, bwid, bar_h);
        cairo_stroke(cr);
        cairo_rectangle(cr, bx, by, bwid * CLAMP(n->mastery, 0, 1), bar_h);
        cairo_fill(cr);
        cairo_set_source_rgb(cr, 0, 0, 0);
    }

    PangoFontDescription *lfd = ui_font(UI_FONT_UI, UI_SMALL_PX - 4, FALSE);
    pango_layout_set_font_description(pl, lfd);
    pango_font_description_free(lfd);
    pango_layout_set_alignment(pl, PANGO_ALIGN_LEFT);
    pango_layout_set_height(pl, -1);
    draw_legend(cr, pl, 0, a.height - legend_h + ui_px(16), a.width);

    g_object_unref(pl);
    cairo_destroy(cr);
    return TRUE;
}

static void map_select(Map *m, int idx)
{
    m->selected = idx;
    gtk_widget_queue_draw(m->area);
    if (idx < 0) {
        ui_label_set(m->info, "Tap a concept to see how well you know it.");
        ui_button_set_disabled(m->explain_btn, TRUE);
        ui_button_set_disabled(m->open_btn, TRUE);
        return;
    }
    Node *n = &g_array_index(m->nodes, Node, idx);
    ui_label_printf(m->info, "<b>%s</b> \302\267 %d%% \302\267 %s\n%s", n->label, pct(n->mastery),
                    status_word(n->status), n->description);
    ui_button_set_disabled(m->explain_btn, n->section == NULL);
    ui_button_set_disabled(m->open_btn, n->section == NULL);
}

static gboolean map_tap(GtkWidget *w, GdkEventButton *ev, gpointer data)
{
    (void)w;
    Map *m = data;
    if (ev->button != 1)
        return FALSE;
    int hit = -1;
    double slack = ui_px(10);
    for (guint i = 0; i < m->nodes->len; i++) {
        Node *n = &g_array_index(m->nodes, Node, i);
        if (ev->x >= n->bx - slack && ev->x <= n->bx + n->bw + slack && ev->y >= n->by - slack &&
            ev->y <= n->by + n->bh + slack)
            hit = (int)i;
    }
    map_select(m, hit);
    return TRUE;
}

static void map_explain(GtkWidget *b, gpointer data)
{
    (void)b;
    Map *m = data;
    if (m->selected < 0)
        return;
    Node *n = &g_array_index(m->nodes, Node, m->selected);
    app_push(screen_explain_new(n->section, "concept", -1, n->id, n->label));
}

static void map_open(GtkWidget *b, gpointer data)
{
    (void)b;
    Map *m = data;
    if (m->selected < 0)
        return;
    Node *n = &g_array_index(m->nodes, Node, m->selected);
    app_push(screen_reader_new(n->section));
}

static void map_loaded(NetResponse *resp, gpointer data)
{
    Map *m = data;
    g_free(m->message);
    m->message = NULL;
    if (resp->error) {
        m->message = g_strdup_printf("Couldn't load the map.\n\n%s", resp->error);
        gtk_widget_queue_draw(m->area);
        return;
    }
    if (!json_bool(resp->json, "concepts_ready", TRUE))
        m->message = g_strdup("The Curriculum Architect is still mapping this document. "
                              "Check back in a moment.");
    json_each(jn, json_get(resp->json, "nodes")) {
        Node n = {0};
        n.id = g_strdup(json_str(jn, "id", ""));
        n.label = g_strdup(json_str(jn, "label", ""));
        n.description = g_strdup(json_str(jn, "description", ""));
        n.status = g_strdup(json_str(jn, "status", "unknown"));
        Json *secs = json_get(jn, "sections");
        n.section = json_len(secs) ? g_strdup(json_at(secs, 0)->str) : NULL;
        n.mastery = json_num(jn, "mastery", 0);
        n.x = json_num(jn, "x", 0.5);
        n.y = json_num(jn, "y", 0.5);
        g_array_append_val(m->nodes, n);
    }
    json_each(je, json_get(resp->json, "edges")) {
        Edge e = {map_find(m, json_str(je, "source", "")), map_find(m, json_str(je, "target", ""))};
        if (e.from >= 0 && e.to >= 0)
            g_array_append_val(m->edges, e);
    }
    map_select(m, -1);
}

GtkWidget *screen_map_new(const char *doc_id)
{
    Map *m = g_new0(Map, 1);
    m->doc_id = g_strdup(doc_id);
    m->nodes = g_array_new(FALSE, TRUE, sizeof(Node));
    m->edges = g_array_new(FALSE, TRUE, sizeof(Edge));
    m->selected = -1;
    m->message = g_strdup("Loading the knowledge graph\342\200\246");
    m->screen = ui_screen();
    g_object_set_data_full(G_OBJECT(m->screen), "ek-state", m, map_free);

    gtk_box_pack_start(GTK_BOX(m->screen), ui_header("Knowledge map", go_back, NULL, NULL, NULL),
                       FALSE, FALSE, 0);
    m->area = gtk_drawing_area_new();
    gtk_widget_add_events(m->area, GDK_BUTTON_PRESS_MASK);
    g_signal_connect(m->area, "expose-event", G_CALLBACK(map_expose), m);
    g_signal_connect(m->area, "button-press-event", G_CALLBACK(map_tap), m);
    gtk_box_pack_start(GTK_BOX(m->screen), m->area, TRUE, TRUE, 0);

    gtk_box_pack_start(GTK_BOX(m->screen), ui_rule(2), FALSE, FALSE, 0);
    m->info = ui_label_wrapped("", UI_FONT_UI, UI_SMALL_PX, FALSE, 0);
    gtk_widget_set_size_request(m->info, ui_content_w(), ui_px(130));
    gtk_misc_set_alignment(GTK_MISC(m->info), 0.0, 0.0);
    gtk_box_pack_start(GTK_BOX(m->screen), m->info, FALSE, FALSE, 0);

    GtkWidget *actions = ui_hbox_equal();
    m->explain_btn = ui_button("Explain concept", UI_BTN_NORMAL, map_explain, m);
    m->open_btn = ui_button("Open section", UI_BTN_PRIMARY, map_open, m);
    gtk_box_pack_start(GTK_BOX(actions), m->explain_btn, TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(actions), m->open_btn, TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(m->screen), actions, FALSE, FALSE, 0);
    map_select(m, -1);

    char *esc = net_escape(doc_id);
    char *path = g_strdup_printf("/api/graph/%s", esc);
    net_get(path, G_OBJECT(m->screen), map_loaded, m);
    g_free(path);
    g_free(esc);
    return m->screen;
}
