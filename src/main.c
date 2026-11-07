#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <gdk/gdkkeysyms.h>

#include "app.h"

/*
 * The Kindle window manager places windows based on a structured title:
 *   L:<layer>_N:<name>_PC:<chrome>_ID:<app id>
 * L:A = application layer. PC:N hides the system status bar, PC:T keeps it.
 */
#define KINDLE_WINDOW_TITLE "L:A_N:application_PC:T_ID:com.edukindle.app"
#define CONFIG_FILE "edukindle.conf"
#define DEFAULT_SERVER "127.0.0.1:8080"
#define TOAST_MS 4500

static GtkWidget *window;
static GtkWidget *stack_box;
static GtkWidget *toast_box;
static GtkWidget *toast_label;
static GList *stack; /* top screen first */
static guint toast_timer;

/* --- navigation ----------------------------------------------------------- */

typedef struct {
    void (*fn)(gpointer);
    gpointer data;
} Resume;

void app_push(GtkWidget *screen)
{
    if (stack)
        gtk_widget_hide(stack->data);
    stack = g_list_prepend(stack, screen);
    gtk_box_pack_start(GTK_BOX(stack_box), screen, TRUE, TRUE, 0);
    gtk_widget_show_all(screen);
}

static void pop_top(void)
{
    if (!stack)
        return;
    GtkWidget *top = stack->data;
    stack = g_list_delete_link(stack, stack);
    gtk_widget_destroy(top);
}

void app_pop(void)
{
    if (!stack || !stack->next)
        return; /* never pop the home screen */
    pop_top();
    GtkWidget *top = stack->data;
    gtk_widget_show(top);
    Resume *r = g_object_get_data(G_OBJECT(top), "ek-resume");
    if (r)
        r->fn(r->data);
}

void app_replace(GtkWidget *screen)
{
    pop_top();
    app_push(screen);
}

void app_on_resume(GtkWidget *screen, void (*fn)(gpointer), gpointer data)
{
    Resume *r = g_new0(Resume, 1);
    r->fn = fn;
    r->data = data;
    g_object_set_data_full(G_OBJECT(screen), "ek-resume", r, g_free);
}

void app_set_pager(GtkWidget *screen, Pager *pager)
{
    g_object_set_data(G_OBJECT(screen), "ek-pager-key", pager);
}

void app_quit(void)
{
    gtk_main_quit();
}

/* --- toast ------------------------------------------------------------------- */

static gboolean toast_hide(gpointer data)
{
    (void)data;
    gtk_widget_hide(toast_box);
    toast_timer = 0;
    return FALSE;
}

void app_toast(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    char *msg = g_strdup_vprintf(fmt, ap);
    va_end(ap);
    gtk_label_set_text(GTK_LABEL(toast_label), msg);
    g_free(msg);
    gtk_widget_show_all(toast_box);
    if (toast_timer)
        g_source_remove(toast_timer);
    toast_timer = g_timeout_add(TOAST_MS, toast_hide, NULL);
}

gboolean app_check(NetResponse *resp)
{
    if (!resp->error)
        return FALSE;
    app_toast("%s", resp->error);
    return TRUE;
}

static gboolean toast_expose(GtkWidget *w, GdkEventExpose *ev, gpointer data)
{
    (void)data;
    cairo_t *cr = gdk_cairo_create(gtk_widget_get_window(w));
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
    gtk_container_propagate_expose(GTK_CONTAINER(w), toast_label, ev);
    return TRUE;
}

static gboolean toast_tap(GtkWidget *w, GdkEventButton *ev, gpointer data)
{
    (void)w;
    (void)ev;
    toast_hide(data);
    return TRUE;
}

/* --- keys --------------------------------------------------------------------- */

static gboolean on_key(GtkWidget *w, GdkEventKey *ev, gpointer data)
{
    (void)w;
    (void)data;
    if (!stack)
        return FALSE;
    Pager *p = g_object_get_data(G_OBJECT(stack->data), "ek-pager-key");
    switch (ev->keyval) {
    case GDK_KEY_Page_Down:
    case GDK_KEY_Right:
    case GDK_KEY_Down:
    case GDK_KEY_space:
        if (p)
            pager_next(p);
        return p != NULL;
    case GDK_KEY_Page_Up:
    case GDK_KEY_Left:
    case GDK_KEY_Up:
        if (p)
            pager_prev(p);
        return p != NULL;
    case GDK_KEY_Escape:
    case GDK_KEY_BackSpace:
        app_pop();
        return TRUE;
    case GDK_KEY_q:
        if (ev->state & GDK_CONTROL_MASK) {
            app_quit();
            return TRUE;
        }
        return FALSE;
    default:
        return FALSE;
    }
}

/* --- startup -------------------------------------------------------------------- */

static char *read_config_server(void)
{
    char *contents = NULL;
    if (!g_file_get_contents(CONFIG_FILE, &contents, NULL, NULL))
        return NULL;
    char *server = NULL;
    char **lines = g_strsplit(contents, "\n", -1);
    for (char **l = lines; *l; l++) {
        char *line = g_strstrip(*l);
        if (g_str_has_prefix(line, "server=") && line[7])
            server = g_strdup(g_strstrip(line + 7));
    }
    g_strfreev(lines);
    g_free(contents);
    return server;
}

void app_save_server(const char *label)
{
    /* Rewrite just the server= line, keeping comments and anything else. */
    char *contents = NULL;
    GString *out = g_string_new(NULL);
    gboolean replaced = FALSE;
    if (g_file_get_contents(CONFIG_FILE, &contents, NULL, NULL)) {
        char **lines = g_strsplit(contents, "\n", -1);
        for (char **l = lines; *l; l++) {
            if (!l[1] && !**l)
                break; /* the empty string after the final newline */
            char *trimmed = g_strstrip(g_strdup(*l));
            if (g_str_has_prefix(trimmed, "server=")) {
                if (!replaced)
                    g_string_append_printf(out, "server=%s\n", label);
                replaced = TRUE;
            } else {
                g_string_append_printf(out, "%s\n", *l);
            }
            g_free(trimmed);
        }
        g_strfreev(lines);
        g_free(contents);
    }
    if (!replaced)
        g_string_append_printf(out, "server=%s\n", label);
    GError *err = NULL;
    if (!g_file_set_contents(CONFIG_FILE, out->str, -1, &err)) {
        g_warning("can't save %s: %s", CONFIG_FILE, err->message);
        g_clear_error(&err);
    }
    g_string_free(out, TRUE);
}

static gboolean on_kindle(void)
{
    return g_file_test("/usr/bin/lipc-get-prop", G_FILE_TEST_EXISTS);
}

int main(int argc, char *argv[])
{
#if !GLIB_CHECK_VERSION(2, 32, 0)
    g_thread_init(NULL);
#endif
    gtk_init(&argc, &argv);

    /* Server: --server ARG > $EDUKINDLE_SERVER > edukindle.conf > default. */
    const char *server = NULL;
    int win_w = 0, win_h = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--server") && i + 1 < argc)
            server = argv[++i];
        else if (!strcmp(argv[i], "--size") && i + 1 < argc)
            sscanf(argv[++i], "%dx%d", &win_w, &win_h);
    }
    char *from_file = NULL;
    if (!server)
        server = g_getenv("EDUKINDLE_SERVER");
    if (!server)
        server = from_file = read_config_server();
    net_init(server ? server : DEFAULT_SERVER);
    g_free(from_file);
    g_message("EduKindle using server %s", net_server());

    GdkScreen *scr = gdk_screen_get_default();
    if (on_kindle() || win_w <= 0) {
        win_w = gdk_screen_get_width(scr);
        win_h = gdk_screen_get_height(scr);
        if (!on_kindle() && win_w > 1000) {
            /* Desktop: a Paperwhite-shaped window. */
            win_h = MIN(win_h - 80, 1100);
            win_w = win_h * 1072 / 1448;
        }
    }
    ui_init(win_w, win_h);
    /* The server renders display maths at the reader's text size and width. */
    net_set_display(ui_px(UI_TEXT_PX + 2), ui_content_w() - ui_px(12));

    window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window), KINDLE_WINDOW_TITLE);
    gtk_window_set_default_size(GTK_WINDOW(window), win_w, win_h);
    ui_paint_background(window);
    g_signal_connect(window, "destroy", G_CALLBACK(gtk_main_quit), NULL);
    g_signal_connect(window, "key-press-event", G_CALLBACK(on_key), NULL);

    GtkWidget *root = gtk_vbox_new(FALSE, 0);
    gtk_container_add(GTK_CONTAINER(window), root);
    stack_box = gtk_vbox_new(FALSE, 0);
    gtk_box_pack_start(GTK_BOX(root), stack_box, TRUE, TRUE, 0);

    toast_box = gtk_event_box_new();
    gtk_widget_add_events(toast_box, GDK_BUTTON_PRESS_MASK);
    toast_label = ui_label_wrapped("", UI_FONT_UI, UI_SMALL_PX, TRUE, 0);
    gtk_misc_set_padding(GTK_MISC(toast_label), ui_px(UI_MARGIN), ui_px(22));
    gtk_widget_modify_fg(toast_label, GTK_STATE_NORMAL, &(GdkColor){0, 0xffff, 0xffff, 0xffff});
    gtk_container_add(GTK_CONTAINER(toast_box), toast_label);
    g_signal_connect(toast_box, "expose-event", G_CALLBACK(toast_expose), NULL);
    g_signal_connect(toast_box, "button-press-event", G_CALLBACK(toast_tap), NULL);
    gtk_box_pack_end(GTK_BOX(root), toast_box, FALSE, FALSE, 0);

    gtk_widget_show(root);
    gtk_widget_show(stack_box);
    app_push(screen_connect_new(TRUE)); /* goes straight to Home if the server answers */
    gtk_widget_show(window);
    gtk_main();
    return 0;
}
