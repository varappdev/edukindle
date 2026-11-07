/* Home (library) and table-of-contents screens. */

#include "app.h"

static const char *id_of(GtkWidget *w)
{
    return g_object_get_data(G_OBJECT(w), "ek-id");
}

static void set_id(GtkWidget *w, const char *id)
{
    g_object_set_data_full(G_OBJECT(w), "ek-id", g_strdup(id), g_free);
}

static void go_back(GtkWidget *b, gpointer data)
{
    (void)b;
    (void)data;
    app_pop();
}

/* --- Home ----------------------------------------------------------------- */

typedef struct {
    GtkWidget *screen;
    GtkWidget *status;
    GtkWidget *body;
} Home;

static void home_load(gpointer data);

static void open_doc(GtkWidget *b, gpointer data)
{
    (void)data;
    app_push(screen_toc_new(id_of(b)));
}

static void open_section(GtkWidget *b, gpointer data)
{
    (void)data;
    app_push(screen_reader_new(id_of(b)));
}

static void retry_home(GtkWidget *b, gpointer data)
{
    (void)b;
    home_load(data);
}

static void change_server(GtkWidget *b, gpointer data)
{
    (void)b;
    (void)data;
    app_push(screen_connect_new(FALSE));
}

static void quit_clicked(GtkWidget *b, gpointer data)
{
    (void)b;
    (void)data;
    app_quit();
}

static void home_loaded(NetResponse *resp, gpointer data)
{
    Home *h = data;
    ui_clear(h->body);

    if (resp->error) {
        ui_label_printf(h->status, "Offline");
        char *msg = g_markup_printf_escaped(
            "<b>Can't reach the study server.</b>\n\n%s\n\n"
            "Check the Kindle is on Wi-Fi and that edukindle.conf points at the "
            "computer running the EduKindle server.",
            resp->error);
        gtk_box_pack_start(GTK_BOX(h->body), ui_message(msg), TRUE, TRUE, 0);
        g_free(msg);
        GtkWidget *row = ui_hbox_equal();
        gtk_box_pack_start(GTK_BOX(row), ui_button("Change server", UI_BTN_NORMAL, change_server,
                                                   NULL), TRUE, TRUE, 0);
        gtk_box_pack_start(GTK_BOX(row), ui_button("Try again", UI_BTN_PRIMARY, retry_home, h),
                           TRUE, TRUE, 0);
        gtk_box_pack_start(GTK_BOX(h->body), row, FALSE, FALSE, 0);
        gtk_widget_show_all(h->body);
        return;
    }

    Json *llm = json_get(resp->json, "llm");
    if (json_bool(llm, "enabled", FALSE))
        ui_label_printf(h->status, "Connected to %s \302\267 AI tutor: %s %s", net_server(),
                        json_str(llm, "provider", ""), json_str(llm, "model", "?"));
    else
        ui_label_printf(h->status, "Connected to %s \302\267 offline tutor (no API key)",
                        net_server());

    Json *cont = json_get(resp->json, "continue");
    if (!json_is_null(cont)) {
        char *m = g_markup_printf_escaped("Continue reading\n<span weight='normal'>%s</span>",
                                          json_str(cont, "title", ""));
        GtkWidget *b = ui_button(m, UI_BTN_PRIMARY, open_section, NULL);
        g_free(m);
        ui_button_set_wrap(b, 0);
        GtkWidget *bl = gtk_bin_get_child(GTK_BIN(b));
        gtk_misc_set_alignment(GTK_MISC(bl), 0.0, 0.5);
        gtk_label_set_justify(GTK_LABEL(bl), GTK_JUSTIFY_LEFT);
        set_id(b, json_str(cont, "section_id", ""));
        gtk_box_pack_start(GTK_BOX(h->body), b, FALSE, FALSE, ui_px(8));
    }

    GtkWidget *heading = ui_label("Your library", UI_FONT_UI, UI_TEXT_PX, TRUE);
    gtk_box_pack_start(GTK_BOX(h->body), heading, FALSE, FALSE, ui_px(10));

    GtkWidget *list = ui_list_new(ui_rows_fitting(json_is_null(cont) ? 560 : 720, 116));
    UiList *l = ui_list_of(list);
    Json *docs = json_get(resp->json, "documents");
    json_each(d, docs) {
        int pct = (int)(json_num(d, "overall", 0) * 100 + 0.5);
        char *m = g_markup_printf_escaped(
            "<b>%s</b>\n<span font_desc='%dpx' weight='normal'>%d of %d sections passed \302\267 "
            "%d%% mastery%s</span>",
            json_str(d, "title", ""), ui_px(UI_SMALL_PX), json_int(d, "passed", 0),
            json_int(d, "sections", 0), pct,
            json_bool(d, "concepts_ready", TRUE) ? "" : " \302\267 mapping\342\200\246");
        GtkWidget *row = ui_button(m, UI_BTN_ROW, open_doc, NULL);
        g_free(m);
        gtk_label_set_ellipsize(GTK_LABEL(gtk_bin_get_child(GTK_BIN(row))), PANGO_ELLIPSIZE_END);
        set_id(row, json_str(d, "id", ""));
        ui_list_add(l, row);
    }
    if (json_len(docs) == 0)
        gtk_box_pack_start(GTK_BOX(h->body),
                           ui_message("No documents yet. Add a PDF from the web dashboard."),
                           FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(h->body), list, TRUE, TRUE, 0);

    char *hint = g_markup_printf_escaped("Add PDFs and see your knowledge graph at "
                                         "http://%s/",
                                         net_server());
    GtkWidget *foot = ui_label_wrapped(hint, UI_FONT_UI, UI_SMALL_PX, FALSE, 0);
    g_free(hint);
    gtk_box_pack_end(GTK_BOX(h->body), foot, FALSE, FALSE, 0);
    gtk_widget_show_all(h->body);
}

static void home_load(gpointer data)
{
    Home *h = data;
    ui_clear(h->body);
    gtk_box_pack_start(GTK_BOX(h->body), ui_message("Connecting\342\200\246"), TRUE, TRUE, 0);
    gtk_widget_show_all(h->body);
    net_get("/api/home", G_OBJECT(h->screen), home_loaded, h);
}

GtkWidget *screen_home_new(void)
{
    Home *h = g_new0(Home, 1);
    h->screen = ui_screen();
    g_object_set_data_full(G_OBJECT(h->screen), "ek-state", h, g_free);

    GtkWidget *right;
    GtkWidget *header = ui_header("EduKindle", NULL, NULL, NULL, &right);
    GtkWidget *quit = ui_button("Exit", UI_BTN_NORMAL, quit_clicked, NULL);
    gtk_widget_set_size_request(quit, ui_px(170), ui_px(UI_TOUCH - 12));
    gtk_box_pack_end(GTK_BOX(right), quit, FALSE, FALSE, 0);
    GtkWidget *server = ui_button("Server", UI_BTN_NORMAL, change_server, NULL);
    gtk_widget_set_size_request(server, ui_px(190), ui_px(UI_TOUCH - 12));
    gtk_box_pack_end(GTK_BOX(right), server, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(h->screen), header, FALSE, FALSE, 0);

    h->status = ui_label_wrapped("", UI_FONT_UI, UI_SMALL_PX, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(h->screen), h->status, FALSE, FALSE, 0);
    h->body = gtk_vbox_new(FALSE, ui_px(16));
    gtk_box_pack_start(GTK_BOX(h->screen), h->body, TRUE, TRUE, 0);

    app_on_resume(h->screen, home_load, h);
    home_load(h);
    return h->screen;
}

/* --- Table of contents ------------------------------------------------------- */

typedef struct {
    GtkWidget *screen;
    char *doc_id;
    GtkWidget *title;
    GtkWidget *summary;
    GtkWidget *list;
} Toc;

static void toc_free(gpointer data)
{
    Toc *t = data;
    g_free(t->doc_id);
    g_free(t);
}

static void toc_progress(GtkWidget *b, gpointer data)
{
    (void)b;
    Toc *t = data;
    app_push(screen_progress_new(t->doc_id));
}

static void toc_map(GtkWidget *b, gpointer data)
{
    (void)b;
    Toc *t = data;
    app_push(screen_map_new(t->doc_id));
}

static const char *status_text(const char *status)
{
    if (!g_strcmp0(status, "passed"))
        return "Passed";
    if (!g_strcmp0(status, "read"))
        return "Started";
    return "Not started";
}

static void toc_loaded(NetResponse *resp, gpointer data)
{
    Toc *t = data;
    if (app_check(resp))
        return;
    Json *j = resp->json;
    ui_label_printf(t->title, "%s", json_str(j, "title", ""));
    ui_label_printf(t->summary, "Mastery %d%% \302\267 %d of %d sections passed",
                    (int)(json_num(j, "overall", 0) * 100 + 0.5), json_int(j, "passed", 0),
                    json_int(j, "total", 0));

    UiList *l = ui_list_of(t->list);
    /* Stay on the same page of the list when refreshing after a quiz. */
    int keep_page = ui_list_page(l);
    ui_list_clear(l);
    const char *last_chapter = NULL;
    json_each(s, json_get(j, "sections")) {
        const char *chapter = json_str(s, "chapter", "");
        Json *best = json_get(s, "best");
        char *score = json_is_null(best) ? g_strdup("")
                                         : g_strdup_printf(" \302\267 best %d/3", (int)best->num);
        char *m = g_markup_printf_escaped(
            "<b>%d. %s</b>\n<span font_desc='%dpx' weight='normal'>%s%s%s%s</span>",
            json_int(s, "index", 0) + 1, json_str(s, "title", ""), ui_px(UI_SMALL_PX),
            status_text(json_str(s, "status", "")), score,
            (chapter[0] && g_strcmp0(chapter, last_chapter)) ? " \302\267 " : "",
            (chapter[0] && g_strcmp0(chapter, last_chapter)) ? chapter : "");
        g_free(score);
        last_chapter = chapter;
        GtkWidget *row = ui_button(m, UI_BTN_ROW, open_section, NULL);
        g_free(m);
        gtk_label_set_ellipsize(GTK_LABEL(gtk_bin_get_child(GTK_BIN(row))), PANGO_ELLIPSIZE_END);
        set_id(row, json_str(s, "id", ""));
        ui_list_add(l, row);
    }
    ui_list_show_page(l, keep_page);
}

static void toc_load(gpointer data)
{
    Toc *t = data;
    char *esc = net_escape(t->doc_id);
    char *path = g_strdup_printf("/api/documents/%s", esc);
    net_get(path, G_OBJECT(t->screen), toc_loaded, t);
    g_free(path);
    g_free(esc);
}

GtkWidget *screen_toc_new(const char *doc_id)
{
    Toc *t = g_new0(Toc, 1);
    t->doc_id = g_strdup(doc_id);
    t->screen = ui_screen();
    g_object_set_data_full(G_OBJECT(t->screen), "ek-state", t, toc_free);

    gtk_box_pack_start(GTK_BOX(t->screen), ui_header("Loading\342\200\246", go_back, NULL,
                                                     &t->title, NULL),
                       FALSE, FALSE, 0);
    t->summary = ui_label("", UI_FONT_UI, UI_SMALL_PX, FALSE);
    gtk_box_pack_start(GTK_BOX(t->screen), t->summary, FALSE, FALSE, 0);

    GtkWidget *row = ui_hbox_equal();
    gtk_box_pack_start(GTK_BOX(row), ui_button("Progress", UI_BTN_NORMAL, toc_progress, t), TRUE,
                       TRUE, 0);
    gtk_box_pack_start(GTK_BOX(row), ui_button("Knowledge map", UI_BTN_NORMAL, toc_map, t), TRUE,
                       TRUE, 0);
    gtk_box_pack_start(GTK_BOX(t->screen), row, FALSE, FALSE, 0);

    t->list = ui_list_new(ui_rows_fitting(600, 116));
    gtk_box_pack_start(GTK_BOX(t->screen), t->list, TRUE, TRUE, 0);

    app_on_resume(t->screen, toc_load, t);
    toc_load(t);
    return t->screen;
}
