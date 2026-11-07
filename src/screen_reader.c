/* Section reader with on-demand explanations, and the explanation viewer. */

#include <string.h>

#include "app.h"

static void go_back(GtkWidget *b, gpointer data)
{
    (void)b;
    (void)data;
    app_pop();
}

/* --- Reader ------------------------------------------------------------------ */

typedef struct {
    GtkWidget *screen;
    char *section_id;
    GtkWidget *title;
    GtkWidget *page_label;
    GtkWidget *meta;
    GtkWidget *hint;
    GtkWidget *explain_btn;
    GtkWidget *body; /* holds the message while loading, then the pager */
    GtkWidget *loading;
    Pager *pager;
    gboolean selecting;
    gboolean layout_pending; /* the AI is still tidying this PDF section */
    guint slow_timer;
} Reader;

static void reader_free(gpointer data)
{
    Reader *r = data;
    if (r->slow_timer)
        g_source_remove(r->slow_timer);
    g_free(r->section_id);
    g_free(r);
}

/* PDF sections are typeset by the AI the first time they're opened. */
static gboolean reader_slow(gpointer data)
{
    Reader *r = data;
    r->slow_timer = 0;
    if (r->loading)
        ui_label_set(r->loading, "<b>Formatting this section for your Kindle\342\200\246</b>\n\n"
                                 "The first time a PDF section is opened, the AI restores its "
                                 "lists, maths and code. This takes a few seconds.");
    return FALSE;
}

static void reader_update_hint(Reader *r)
{
    if (!r->pager)
        return;
    int page = pager_page(r->pager), pages = pager_pages(r->pager);
    char *pg = g_strdup_printf("%d/%d", page + 1, pages);
    gtk_label_set_text(GTK_LABEL(r->page_label), pg);
    g_free(pg);

    if (r->selecting)
        ui_label_set(r->hint, "<b>Tap the paragraph you want explained.</b>");
    else if (r->layout_pending && page == 0)
        ui_label_set(r->hint, "This section is still being formatted. Reopen it in a minute for "
                              "the tidied version.");
    else if (page == pages - 1)
        ui_label_set(r->hint, "End of section. Tap <b>Check</b> to test your understanding.");
    else if (page == 0)
        ui_label_set(r->hint, "Tap the right side of the page to turn, the left to go back.");
    else
        ui_label_set(r->hint, "");
}

static void reader_page_changed(Pager *p, gpointer data)
{
    (void)p;
    reader_update_hint(data);
}

static void reader_set_selecting(Reader *r, gboolean on)
{
    r->selecting = on;
    if (r->pager)
        pager_set_select_mode(r->pager, on);
    ui_button_set_selected(r->explain_btn, on);
    reader_update_hint(r);
}

/* The pager reports which source paragraph was tapped; the server explains it. */
static void reader_paragraph_tapped(Pager *p, int para, gpointer data)
{
    (void)p;
    Reader *r = data;
    reader_set_selecting(r, FALSE);
    app_push(screen_explain_new(r->section_id, "paragraph", para, NULL, NULL));
}

static void reader_summary(GtkWidget *b, gpointer data)
{
    (void)b;
    Reader *r = data;
    reader_set_selecting(r, FALSE);
    app_push(screen_explain_new(r->section_id, "summary", -1, NULL, NULL));
}

static void reader_simple(GtkWidget *b, gpointer data)
{
    (void)b;
    Reader *r = data;
    reader_set_selecting(r, FALSE);
    app_push(screen_explain_new(r->section_id, "simple", -1, NULL, NULL));
}

static void reader_explain(GtkWidget *b, gpointer data)
{
    (void)b;
    Reader *r = data;
    if (r->pager)
        reader_set_selecting(r, !r->selecting);
}

static void reader_quiz(GtkWidget *b, gpointer data)
{
    (void)b;
    Reader *r = data;
    reader_set_selecting(r, FALSE);
    app_push(screen_quiz_new(r->section_id));
}

static void reader_loaded(NetResponse *resp, gpointer data)
{
    Reader *r = data;
    if (r->slow_timer) {
        g_source_remove(r->slow_timer);
        r->slow_timer = 0;
    }
    r->loading = NULL;
    ui_clear(r->body);
    if (resp->error) {
        char *m = g_markup_printf_escaped("<b>Couldn't open this section.</b>\n\n%s",
                                          resp->error);
        gtk_box_pack_start(GTK_BOX(r->body), ui_message(m), TRUE, TRUE, 0);
        g_free(m);
        gtk_widget_show_all(r->body);
        return;
    }
    Json *j = resp->json;
    r->layout_pending = json_bool(j, "layout_pending", FALSE);
    ui_label_printf(r->title, "%s", json_str(j, "title", ""));
    const char *chapter = json_str(j, "chapter", "");
    ui_label_printf(r->meta, "%s%sSection %d of %d", chapter, chapter[0] ? " \302\267 " : "",
                    json_int(j, "index", 0) + 1, json_int(j, "total", 0));

    GtkWidget *area = pager_new(UI_FONT_READ, UI_TEXT_PX + 2);
    r->pager = pager_of(area);
    pager_on_change(r->pager, reader_page_changed, r);
    pager_on_select(r->pager, reader_paragraph_tapped, r);
    Json *doc = json_get(j, "doc");
    if (doc)
        pager_set_doc(r->pager, doc);
    else
        pager_set_text(r->pager, json_str(j, "text", ""));
    app_set_pager(r->screen, r->pager);
    gtk_box_pack_start(GTK_BOX(r->body), area, TRUE, TRUE, 0);
    gtk_widget_show_all(r->body);
    reader_update_hint(r);
}

GtkWidget *screen_reader_new(const char *section_id)
{
    Reader *r = g_new0(Reader, 1);
    r->section_id = g_strdup(section_id);
    r->screen = ui_screen();
    g_object_set_data_full(G_OBJECT(r->screen), "ek-state", r, reader_free);

    GtkWidget *right;
    gtk_box_pack_start(GTK_BOX(r->screen),
                       ui_header("Loading\342\200\246", go_back, NULL, &r->title, &right), FALSE,
                       FALSE, 0);
    r->page_label = ui_label("", UI_FONT_UI, UI_SMALL_PX, TRUE);
    gtk_box_pack_end(GTK_BOX(right), r->page_label, FALSE, FALSE, 0);

    r->meta = ui_label("", UI_FONT_UI, UI_SMALL_PX, FALSE);
    gtk_label_set_ellipsize(GTK_LABEL(r->meta), PANGO_ELLIPSIZE_END);
    gtk_box_pack_start(GTK_BOX(r->screen), r->meta, FALSE, FALSE, 0);

    r->body = gtk_vbox_new(FALSE, 0);
    r->loading = ui_message("Opening section\342\200\246");
    gtk_box_pack_start(GTK_BOX(r->body), r->loading, TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(r->screen), r->body, TRUE, TRUE, 0);
    r->slow_timer = g_timeout_add(1500, reader_slow, r);

    r->hint = ui_label_wrapped("", UI_FONT_UI, UI_SMALL_PX, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(r->screen), r->hint, FALSE, FALSE, 0);

    GtkWidget *tools = ui_hbox_equal();
    gtk_box_pack_start(GTK_BOX(tools), ui_button("Summary", UI_BTN_NORMAL, reader_summary, r),
                       TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(tools), ui_button("Simplify", UI_BTN_NORMAL, reader_simple, r),
                       TRUE, TRUE, 0);
    r->explain_btn = ui_button("Explain \302\266", UI_BTN_NORMAL, reader_explain, r);
    gtk_box_pack_start(GTK_BOX(tools), r->explain_btn, TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(tools), ui_button("Check", UI_BTN_PRIMARY, reader_quiz, r), TRUE,
                       TRUE, 0);
    gtk_box_pack_end(GTK_BOX(r->screen), tools, FALSE, FALSE, 0);

    char *esc = net_escape(section_id);
    char *path = g_strdup_printf("/api/sections/%s", esc);
    net_get(path, G_OBJECT(r->screen), reader_loaded, r);
    g_free(path);
    g_free(esc);
    return r->screen;
}

/* --- Explanation viewer -------------------------------------------------------- */

typedef struct {
    GtkWidget *screen;
    GtkWidget *title;
    GtkWidget *page_label;
    GtkWidget *body;
    Pager *pager;
} Explain;

static void explain_page_changed(Pager *p, gpointer data)
{
    Explain *e = data;
    char *pg = g_strdup_printf("%d/%d", pager_page(p) + 1, pager_pages(p));
    gtk_label_set_text(GTK_LABEL(e->page_label), pg);
    g_free(pg);
}

static void explain_loaded(NetResponse *resp, gpointer data)
{
    Explain *e = data;
    ui_clear(e->body);
    if (resp->error) {
        char *m = g_markup_printf_escaped("<b>No explanation this time.</b>\n\n%s", resp->error);
        gtk_box_pack_start(GTK_BOX(e->body), ui_message(m), TRUE, TRUE, 0);
        g_free(m);
        gtk_widget_show_all(e->body);
        return;
    }
    ui_label_printf(e->title, "%s", json_str(resp->json, "title", "Explanation"));
    GtkWidget *area = pager_new(UI_FONT_READ, UI_TEXT_PX + 2);
    e->pager = pager_of(area);
    pager_on_change(e->pager, explain_page_changed, e);
    Json *doc = json_get(resp->json, "doc");
    if (doc)
        pager_set_doc(e->pager, doc);
    else
        pager_set_text(e->pager, json_str(resp->json, "body", ""));
    app_set_pager(e->screen, e->pager);
    gtk_box_pack_start(GTK_BOX(e->body), area, TRUE, TRUE, 0);
    gtk_widget_show_all(e->body);
}

GtkWidget *screen_explain_new(const char *section_id, const char *mode, int paragraph,
                              const char *concept_id, const char *concept_label)
{
    Explain *e = g_new0(Explain, 1);
    e->screen = ui_screen();
    g_object_set_data_full(G_OBJECT(e->screen), "ek-state", e, g_free);

    const char *title = !strcmp(mode, "summary")     ? "Key points"
                        : !strcmp(mode, "simple")    ? "In simple terms"
                        : !strcmp(mode, "paragraph") ? "This paragraph"
                                                     : concept_label;
    GtkWidget *right;
    gtk_box_pack_start(GTK_BOX(e->screen), ui_header(title, go_back, NULL, &e->title, &right),
                       FALSE, FALSE, 0);
    e->page_label = ui_label("", UI_FONT_UI, UI_SMALL_PX, TRUE);
    gtk_box_pack_end(GTK_BOX(right), e->page_label, FALSE, FALSE, 0);

    e->body = gtk_vbox_new(FALSE, 0);
    gtk_box_pack_start(GTK_BOX(e->body),
                       ui_message("<b>The Socratic Educator is thinking\342\200\246</b>\n\n"
                                  "This usually takes a few seconds."),
                       TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(e->screen), e->body, TRUE, TRUE, 0);

    GString *body = g_string_new("{\"section_id\":");
    json_append_string(body, section_id);
    g_string_append(body, ",\"mode\":");
    json_append_string(body, mode);
    if (paragraph >= 0)
        g_string_append_printf(body, ",\"paragraph\":%d", paragraph);
    if (concept_id) {
        g_string_append(body, ",\"concept_id\":");
        json_append_string(body, concept_id);
    }
    g_string_append_c(body, '}');
    net_post("/api/explain", body->str, G_OBJECT(e->screen), explain_loaded, e);
    g_string_free(body, TRUE);
    return e->screen;
}
