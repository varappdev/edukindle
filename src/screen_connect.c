/* Server connection screen: the first screen at startup, and reachable from
 * Home's "Server" button. It tries the saved address first; if that fails it
 * searches the network, and the student can always type the laptop's address
 * on a large keypad. Whatever connects is saved to edukindle.conf. */

#include <stdlib.h>
#include <string.h>

#include "app.h"

#define PROBE_TIMEOUT_S 5

typedef struct {
    GtkWidget *screen;
    gboolean startup;
    GString *addr;      /* what's in the address field */
    char *probing;      /* normalised address being checked, NULL when idle */
    gboolean edited;    /* the field changed while a check was running */
    gboolean searching;
    GtkWidget *field;
    GtkWidget *status;
    GtkWidget *found_box;
    GtkWidget *connect_btn;
    GtkWidget *find_btn;
} Connect;

static void connect_free(gpointer data)
{
    Connect *c = data;
    g_string_free(c->addr, TRUE);
    g_free(c->probing);
    g_free(c);
}

static void connect_sync(Connect *c)
{
    char *m = g_markup_printf_escaped("<b>%s</b>_", c->addr->str);
    ui_label_set(c->field, m);
    g_free(m);
    ui_button_set_disabled(c->connect_btn, c->probing != NULL || c->addr->len == 0);
    ui_button_set_disabled(c->find_btn, c->searching);
}

static void connect_status(Connect *c, const char *markup)
{
    ui_label_set(c->status, markup);
}

static void connect_search(Connect *c);

static void connect_done(Connect *c, const char *label)
{
    net_init(label);
    app_save_server(label);
    if (c->startup)
        app_replace(screen_home_new());
    else
        app_pop(); /* Home reloads from the new server when it resumes */
}

/* Turns a socket error into advice: the reason says what's actually wrong. */
static char *failure_advice(const char *label, NetResponse *resp)
{
    const char *port = strrchr(label, ':') + 1;
    const char *err = resp->error ? resp->error : "";
    if (!resp->error)
        return g_strdup("Something answered there, but it isn't an EduKindle server.");
    if (resp->status != 0)
        return g_strdup(err); /* reached a server, which returned an error */
    if (strstr(err, "refused"))
        return g_strdup_printf("Nothing is listening on port %s there. Is the EduKindle "
                               "server running, and is that the port it printed?", port);
    if (strstr(err, "timed out") || strstr(err, "Timeout"))
        return g_strdup_printf("No reply. The computer's firewall may be blocking port %s, "
                               "or this network keeps devices apart (eduroam and most "
                               "campus Wi-Fi do). A phone hotspot avoids that.", port);
    if (strstr(err, "nreachable") || strstr(err, "No route"))
        return g_strdup("That address isn't on this network. Join the same Wi-Fi or hotspot "
                        "as the computer.");
    return g_strdup(err);
}

static void probe_done(NetResponse *resp, gpointer data)
{
    Connect *c = data;
    char *label = c->probing;
    c->probing = NULL;
    gboolean ok = !resp->error && json_bool(resp->json, "ok", FALSE);

    if (ok && !c->edited) {
        connect_done(c, label); /* may destroy this screen */
        g_free(label);
        return;
    }
    if (ok) {
        connect_status(c, "Found a server there, but you've changed the address since. "
                          "Tap <b>Connect</b> to use the new one.");
    } else {
        char *advice = failure_advice(label, resp);
        char *m = g_markup_printf_escaped("<b>Couldn't connect to %s.</b> %s", label, advice);
        connect_status(c, m);
        g_free(m);
        g_free(advice);
    }
    g_free(label);
    connect_sync(c);
    if (!ok && c->startup && !c->edited && !c->searching)
        connect_search(c); /* the saved address is stale, e.g. a new hotspot IP */
}

static void connect_probe(Connect *c, const char *text)
{
    char *label = net_normalise_server(text);
    if (!label) {
        connect_status(c, "<b>That isn't an address.</b> Type it like "
                          "<b>192.168.43.20:8080</b> (the port is optional).");
        return;
    }
    g_free(c->probing);
    c->probing = label;
    c->edited = FALSE;
    char *m = g_markup_printf_escaped("Connecting to <b>%s</b>\342\200\246", label);
    connect_status(c, m);
    g_free(m);
    connect_sync(c);
    net_request_full(label, "GET", "/api/health", NULL, PROBE_TIMEOUT_S, G_OBJECT(c->screen),
                     probe_done, c);
}

static void use_found(GtkWidget *b, gpointer data)
{
    Connect *c = data;
    const char *label = g_object_get_data(G_OBJECT(b), "ek-addr");
    g_string_assign(c->addr, label);
    connect_probe(c, label);
}

static void search_done(GPtrArray *found, const char *note, gpointer data)
{
    Connect *c = data;
    c->searching = FALSE;
    ui_clear(c->found_box);

    if (found->len == 1 && !c->edited && !c->probing) {
        const char *label = g_ptr_array_index(found, 0);
        g_string_assign(c->addr, label);
        connect_probe(c, label);
        return;
    }
    if (found->len == 0) {
        char *m = g_markup_printf_escaped(
            "<b>No EduKindle server found.</b> %s Is the server running, with its port open in "
            "the computer's firewall? You can also type the address it printed.",
            note ? note : "");
        connect_status(c, m);
        g_free(m);
    } else {
        connect_status(c, "<b>Several servers found.</b> Tap the one to use:");
        for (guint i = 0; i < found->len; i++) {
            const char *label = g_ptr_array_index(found, i);
            GtkWidget *b = ui_button(label, UI_BTN_ROW, use_found, c);
            g_object_set_data_full(G_OBJECT(b), "ek-addr", g_strdup(label), g_free);
            gtk_box_pack_start(GTK_BOX(c->found_box), b, FALSE, FALSE, 0);
        }
        gtk_widget_show_all(c->found_box);
    }
    connect_sync(c);
}

static void connect_search(Connect *c)
{
    if (c->searching)
        return;
    char *label = net_normalise_server(c->addr->str);
    guint16 port = NET_DEFAULT_PORT;
    if (label)
        port = (guint16)atoi(strrchr(label, ':') + 1);
    g_free(label);
    c->searching = TRUE;
    char *m = g_strdup_printf("Searching this network for an EduKindle server on port %u\342\200\246",
                              port);
    connect_status(c, m);
    g_free(m);
    connect_sync(c);
    net_discover(port, G_OBJECT(c->screen), search_done, c);
}

static void on_key(GtkWidget *b, gpointer data)
{
    Connect *c = data;
    const char *key = g_object_get_data(G_OBJECT(b), "ek-key");
    if (!strcmp(key, "del")) {
        if (c->addr->len)
            g_string_truncate(c->addr, c->addr->len - 1);
    } else if (!strcmp(key, "clear")) {
        g_string_truncate(c->addr, 0);
    } else if (c->addr->len < 40) {
        g_string_append(c->addr, key);
    }
    c->edited = TRUE;
    connect_sync(c);
}

static void on_connect(GtkWidget *b, gpointer data)
{
    (void)b;
    Connect *c = data;
    connect_probe(c, c->addr->str);
}

static void on_find(GtkWidget *b, gpointer data)
{
    (void)b;
    connect_search(data);
}

static void exit_clicked(GtkWidget *b, gpointer data)
{
    (void)b;
    (void)data;
    app_quit();
}

static void on_back(GtkWidget *b, gpointer data)
{
    (void)b;
    (void)data;
    app_pop();
}

static GtkWidget *keypad(Connect *c)
{
    static const char *keys[] = {"1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", ":"};
    GtkWidget *table = gtk_table_new(5, 6, TRUE);
    gtk_table_set_row_spacings(GTK_TABLE(table), ui_px(12));
    gtk_table_set_col_spacings(GTK_TABLE(table), ui_px(12));
    for (int i = 0; i < 12; i++) {
        GtkWidget *b = ui_button(keys[i], UI_BTN_NORMAL, on_key, c);
        ui_button_set_font(b, UI_FONT_UI, UI_TITLE_PX, TRUE);
        g_object_set_data(G_OBJECT(b), "ek-key", (gpointer)keys[i]);
        int row = i / 3, col = (i % 3) * 2;
        gtk_table_attach_defaults(GTK_TABLE(table), b, col, col + 2, row, row + 1);
    }
    GtkWidget *del = ui_button("Delete", UI_BTN_NORMAL, on_key, c);
    g_object_set_data(G_OBJECT(del), "ek-key", "del");
    gtk_table_attach_defaults(GTK_TABLE(table), del, 0, 3, 4, 5);
    GtkWidget *clear = ui_button("Clear", UI_BTN_NORMAL, on_key, c);
    g_object_set_data(G_OBJECT(clear), "ek-key", "clear");
    gtk_table_attach_defaults(GTK_TABLE(table), clear, 3, 6, 4, 5);
    return table;
}

GtkWidget *screen_connect_new(gboolean startup)
{
    Connect *c = g_new0(Connect, 1);
    c->startup = startup;
    c->addr = g_string_new(net_server());
    c->screen = ui_screen();
    g_object_set_data_full(G_OBJECT(c->screen), "ek-state", c, connect_free);

    GtkWidget *right;
    gtk_box_pack_start(GTK_BOX(c->screen),
                       ui_header(startup ? "EduKindle" : "Study server", startup ? NULL : on_back,
                                 NULL, NULL, &right),
                       FALSE, FALSE, 0);
    if (startup) {
        GtkWidget *exit = ui_button("Exit", UI_BTN_NORMAL, exit_clicked, NULL);
        gtk_widget_set_size_request(exit, ui_px(170), ui_px(UI_TOUCH - 12));
        gtk_box_pack_end(GTK_BOX(right), exit, FALSE, FALSE, 0);
    }

    c->status = ui_label_wrapped("", UI_FONT_UI, UI_SMALL_PX, FALSE, 0);
    gtk_widget_set_size_request(c->status, ui_content_w(), ui_px(175)); /* four lines */
    gtk_misc_set_alignment(GTK_MISC(c->status), 0.0, 0.0);
    gtk_box_pack_start(GTK_BOX(c->screen), c->status, FALSE, FALSE, 0);

    GtkWidget *caption = ui_label("Your computer's address", UI_FONT_UI, UI_SMALL_PX, TRUE);
    gtk_box_pack_start(GTK_BOX(c->screen), caption, FALSE, FALSE, 0);
    c->field = ui_label("", UI_FONT_MONO, UI_TITLE_PX + 6, FALSE);
    gtk_label_set_ellipsize(GTK_LABEL(c->field), PANGO_ELLIPSIZE_START);
    gtk_box_pack_start(GTK_BOX(c->screen), ui_frame(c->field, 3, FALSE), FALSE, FALSE, 0);

    gtk_box_pack_start(GTK_BOX(c->screen), keypad(c), FALSE, FALSE, ui_px(8));
    c->found_box = gtk_vbox_new(FALSE, 0);
    gtk_box_pack_start(GTK_BOX(c->screen), c->found_box, FALSE, FALSE, 0);

    GtkWidget *tip = ui_label_wrapped(
        "The server prints its address when it starts. On a phone hotspot, join the "
        "hotspot on both the Kindle and the computer.",
        UI_FONT_UI, UI_SMALL_PX - 2, FALSE, 0);
    gtk_box_pack_end(GTK_BOX(c->screen), tip, FALSE, FALSE, 0);
    GtkWidget *actions = ui_hbox_equal();
    c->find_btn = ui_button("Find automatically", UI_BTN_NORMAL, on_find, c);
    c->connect_btn = ui_button("Connect", UI_BTN_PRIMARY, on_connect, c);
    gtk_box_pack_start(GTK_BOX(actions), c->find_btn, TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(actions), c->connect_btn, TRUE, TRUE, 0);
    gtk_box_pack_end(GTK_BOX(c->screen), actions, FALSE, FALSE, 0);

    connect_sync(c);
    if (startup)
        connect_probe(c, c->addr->str); /* usually the saved server is right: go straight in */
    else
        connect_status(c, "Type your computer's address, or let EduKindle find it.");
    return c->screen;
}
