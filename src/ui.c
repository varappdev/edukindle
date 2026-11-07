#include "ui.h"

#include <math.h>
#include <string.h>

/* Kindle fonts first, then common desktop fallbacks for testing on a PC. */
#define FONT_UI "Amazon Ember, Helvetica Neue LT, Futura, DejaVu Sans, Sans"
#define FONT_READ "Bookerly, Caecilia, Georgia, DejaVu Serif, Serif"

static int screen_w = 1072, screen_h = 1448;
static double scale = 1.0;

static const GdkColor BLACK = {0, 0, 0, 0};
static const GdkColor WHITE = {0, 0xffff, 0xffff, 0xffff};
static const GdkColor GREY = {0, 0x7777, 0x7777, 0x7777};

void ui_init(int w, int h)
{
    screen_w = w;
    screen_h = h;
    scale = w / UI_DESIGN_W;

    /* Neutralise the theme for anything we don't draw ourselves. */
    gtk_rc_parse_string("style \"ek\" {\n"
                        "  bg[NORMAL] = \"#ffffff\" bg[PRELIGHT] = \"#ffffff\"\n"
                        "  bg[ACTIVE] = \"#ffffff\" bg[SELECTED] = \"#000000\"\n"
                        "  fg[NORMAL] = \"#000000\" fg[PRELIGHT] = \"#000000\"\n"
                        "  fg[ACTIVE] = \"#000000\" fg[INSENSITIVE] = \"#777777\"\n"
                        "  text[NORMAL] = \"#000000\" base[NORMAL] = \"#ffffff\"\n"
                        "  xthickness = 0 ythickness = 0\n"
                        "}\n"
                        "widget \"*\" style \"ek\"\n");
}

int ui_px(double design_px)
{
    int v = (int)lround(design_px * scale);
    return v < 1 && design_px > 0 ? 1 : v;
}

int ui_screen_w(void) { return screen_w; }
int ui_screen_h(void) { return screen_h; }
int ui_content_w(void) { return screen_w - 2 * ui_px(UI_MARGIN); }

PangoFontDescription *ui_font(UiFace face, double px, gboolean bold)
{
    PangoFontDescription *fd = pango_font_description_new();
    pango_font_description_set_family(fd, face == UI_FONT_READ ? FONT_READ : FONT_UI);
    pango_font_description_set_absolute_size(fd, ui_px(px) * PANGO_SCALE);
    pango_font_description_set_weight(fd, bold ? PANGO_WEIGHT_BOLD : PANGO_WEIGHT_NORMAL);
    return fd;
}

void ui_set_font(GtkWidget *w, UiFace face, double px, gboolean bold)
{
    PangoFontDescription *fd = ui_font(face, px, bold);
    gtk_widget_modify_font(w, fd);
    pango_font_description_free(fd);
}

void ui_paint_background(GtkWidget *w)
{
    gtk_widget_modify_bg(w, GTK_STATE_NORMAL, &WHITE);
}

GtkWidget *ui_label(const char *markup, UiFace face, double px, gboolean bold)
{
    GtkWidget *l = gtk_label_new(NULL);
    gtk_label_set_markup(GTK_LABEL(l), markup ? markup : "");
    gtk_misc_set_alignment(GTK_MISC(l), 0.0, 0.5);
    ui_set_font(l, face, px, bold);
    gtk_widget_modify_fg(l, GTK_STATE_NORMAL, &BLACK);
    return l;
}

GtkWidget *ui_label_wrapped(const char *markup, UiFace face, double px, gboolean bold,
                            int wrap_w)
{
    GtkWidget *l = ui_label(markup, face, px, bold);
    gtk_label_set_line_wrap(GTK_LABEL(l), TRUE);
    gtk_label_set_line_wrap_mode(GTK_LABEL(l), PANGO_WRAP_WORD_CHAR);
    gtk_widget_set_size_request(l, wrap_w > 0 ? wrap_w : ui_content_w(), -1);
    return l;
}

void ui_label_set(GtkWidget *label, const char *markup)
{
    gtk_label_set_markup(GTK_LABEL(label), markup ? markup : "");
}

void ui_label_printf(GtkWidget *label, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    char *s = g_markup_vprintf_escaped(fmt, ap);
    va_end(ap);
    gtk_label_set_markup(GTK_LABEL(label), s);
    g_free(s);
}

/* --- Buttons ---------------------------------------------------------------- */

typedef struct {
    UiButtonStyle style;
    gboolean pressed;
    gboolean selected;
    gboolean disabled;
    UiClickFn fn;
    gpointer data;
    GtkWidget *label;
} Button;

static Button *button_of(GtkWidget *w)
{
    return g_object_get_data(G_OBJECT(w), "ek-button");
}

static gboolean button_inverted(Button *b)
{
    if (b->disabled)
        return FALSE;
    gboolean dark = b->style == UI_BTN_PRIMARY;
    if (b->selected)
        dark = TRUE;
    if (b->pressed && !b->disabled)
        dark = !dark;
    return dark;
}

static void button_sync_label(Button *b)
{
    const GdkColor *c = b->disabled ? &GREY : button_inverted(b) ? &WHITE : &BLACK;
    gtk_widget_modify_fg(b->label, GTK_STATE_NORMAL, c);
}

static gboolean button_expose(GtkWidget *w, GdkEventExpose *ev, gpointer unused)
{
    (void)unused;
    Button *b = button_of(w);
    GtkAllocation a;
    gtk_widget_get_allocation(w, &a);

    cairo_t *cr = gdk_cairo_create(gtk_widget_get_window(w));
    gdk_cairo_region(cr, ev->region);
    cairo_clip(cr);

    gboolean dark = button_inverted(b);
    cairo_set_source_rgb(cr, dark ? 0 : 1, dark ? 0 : 1, dark ? 0 : 1);
    cairo_paint(cr);

    double lw = ui_px(b->selected ? 5 : 3);
    cairo_set_source_rgb(cr, b->disabled ? 0.55 : 0, b->disabled ? 0.55 : 0,
                         b->disabled ? 0.55 : 0);
    switch (b->style) {
    case UI_BTN_NORMAL:
    case UI_BTN_PRIMARY: {
        double r = ui_px(10), x = lw / 2, y = lw / 2, ww = a.width - lw, hh = a.height - lw;
        cairo_new_sub_path(cr);
        cairo_arc(cr, x + ww - r, y + r, r, -G_PI / 2, 0);
        cairo_arc(cr, x + ww - r, y + hh - r, r, 0, G_PI / 2);
        cairo_arc(cr, x + r, y + hh - r, r, G_PI / 2, G_PI);
        cairo_arc(cr, x + r, y + r, r, G_PI, 3 * G_PI / 2);
        cairo_close_path(cr);
        cairo_set_line_width(cr, lw);
        cairo_stroke(cr);
        break;
    }
    case UI_BTN_ROW:
        cairo_rectangle(cr, 0, a.height - ui_px(2), a.width, ui_px(2));
        cairo_fill(cr);
        break;
    case UI_BTN_QUIET:
        break;
    }
    cairo_destroy(cr);

    GtkWidget *child = gtk_bin_get_child(GTK_BIN(w));
    if (child)
        gtk_container_propagate_expose(GTK_CONTAINER(w), child, ev);
    return TRUE;
}

static gboolean button_press(GtkWidget *w, GdkEventButton *ev, gpointer unused)
{
    (void)unused;
    Button *b = button_of(w);
    if (ev->button != 1 || b->disabled)
        return FALSE;
    b->pressed = TRUE;
    button_sync_label(b);
    gtk_widget_queue_draw(w);
    return TRUE;
}

static gboolean button_release(GtkWidget *w, GdkEventButton *ev, gpointer unused)
{
    (void)unused;
    Button *b = button_of(w);
    if (ev->button != 1 || !b->pressed)
        return FALSE;
    b->pressed = FALSE;
    button_sync_label(b);
    gtk_widget_queue_draw(w);

    GtkAllocation a;
    gtk_widget_get_allocation(w, &a);
    gboolean inside = ev->x >= 0 && ev->y >= 0 && ev->x < a.width && ev->y < a.height;
    if (inside && b->fn) {
        /* The handler may navigate away and destroy this button. */
        g_object_ref(w);
        b->fn(w, b->data);
        g_object_unref(w);
    }
    return TRUE;
}

static gboolean button_leave(GtkWidget *w, GdkEventCrossing *ev, gpointer unused)
{
    (void)ev;
    (void)unused;
    Button *b = button_of(w);
    if (b->pressed) {
        b->pressed = FALSE;
        button_sync_label(b);
        gtk_widget_queue_draw(w);
    }
    return FALSE;
}

GtkWidget *ui_button(const char *markup, UiButtonStyle style, UiClickFn fn, gpointer data)
{
    Button *b = g_new0(Button, 1);
    b->style = style;
    b->fn = fn;
    b->data = data;

    GtkWidget *w = gtk_event_box_new();
    g_object_set_data_full(G_OBJECT(w), "ek-button", b, g_free);
    gtk_widget_add_events(w, GDK_BUTTON_PRESS_MASK | GDK_BUTTON_RELEASE_MASK |
                                 GDK_LEAVE_NOTIFY_MASK);
    gtk_widget_set_size_request(w, -1, ui_px(UI_TOUCH));

    b->label = ui_label(markup, UI_FONT_UI, UI_BUTTON_PX, style != UI_BTN_ROW);
    if (style == UI_BTN_ROW) {
        gtk_misc_set_alignment(GTK_MISC(b->label), 0.0, 0.5);
        gtk_misc_set_padding(GTK_MISC(b->label), ui_px(8), ui_px(18));
    } else {
        gtk_misc_set_alignment(GTK_MISC(b->label), 0.5, 0.5);
        gtk_label_set_justify(GTK_LABEL(b->label), GTK_JUSTIFY_CENTER);
        gtk_misc_set_padding(GTK_MISC(b->label), ui_px(20), ui_px(14));
    }
    gtk_container_add(GTK_CONTAINER(w), b->label);
    button_sync_label(b);

    g_signal_connect(w, "expose-event", G_CALLBACK(button_expose), NULL);
    g_signal_connect(w, "button-press-event", G_CALLBACK(button_press), NULL);
    g_signal_connect(w, "button-release-event", G_CALLBACK(button_release), NULL);
    g_signal_connect(w, "leave-notify-event", G_CALLBACK(button_leave), NULL);
    return w;
}

void ui_button_set_markup(GtkWidget *w, const char *markup)
{
    gtk_label_set_markup(GTK_LABEL(button_of(w)->label), markup);
}

void ui_button_set_selected(GtkWidget *w, gboolean selected)
{
    Button *b = button_of(w);
    if (b->selected == selected)
        return;
    b->selected = selected;
    button_sync_label(b);
    gtk_widget_queue_draw(w);
}

gboolean ui_button_get_selected(GtkWidget *w)
{
    return button_of(w)->selected;
}

void ui_button_set_disabled(GtkWidget *w, gboolean disabled)
{
    Button *b = button_of(w);
    b->disabled = disabled;
    b->pressed = FALSE;
    button_sync_label(b);
    gtk_widget_queue_draw(w);
}

void ui_button_set_wrap(GtkWidget *w, int width)
{
    Button *b = button_of(w);
    int pad = 2 * ui_px(28);
    gtk_label_set_line_wrap(GTK_LABEL(b->label), TRUE);
    gtk_label_set_line_wrap_mode(GTK_LABEL(b->label), PANGO_WRAP_WORD_CHAR);
    gtk_widget_set_size_request(b->label, (width > 0 ? width : ui_content_w()) - pad, -1);
}

void ui_button_set_font(GtkWidget *w, UiFace face, double px, gboolean bold)
{
    ui_set_font(button_of(w)->label, face, px, bold);
}

/* --- Layout helpers ---------------------------------------------------------- */

static gboolean rule_expose(GtkWidget *w, GdkEventExpose *ev, gpointer unused)
{
    (void)ev;
    (void)unused;
    cairo_t *cr = gdk_cairo_create(gtk_widget_get_window(w));
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
    return TRUE;
}

GtkWidget *ui_rule(double thickness)
{
    GtkWidget *w = gtk_drawing_area_new();
    gtk_widget_set_size_request(w, -1, ui_px(thickness));
    g_signal_connect(w, "expose-event", G_CALLBACK(rule_expose), NULL);
    return w;
}

typedef struct {
    double border;
    gboolean dashed;
} Frame;

static gboolean frame_expose(GtkWidget *w, GdkEventExpose *ev, gpointer data)
{
    Frame *f = data;
    GtkAllocation a;
    gtk_widget_get_allocation(w, &a);
    cairo_t *cr = gdk_cairo_create(gtk_widget_get_window(w));
    gdk_cairo_region(cr, ev->region);
    cairo_clip(cr);
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_paint(cr);
    double lw = ui_px(f->border);
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_set_line_width(cr, lw);
    if (f->dashed) {
        double dash[] = {ui_px(14), ui_px(8)};
        cairo_set_dash(cr, dash, 2, 0);
    }
    cairo_rectangle(cr, lw / 2, lw / 2, a.width - lw, a.height - lw);
    cairo_stroke(cr);
    cairo_destroy(cr);
    gtk_container_propagate_expose(GTK_CONTAINER(w), gtk_bin_get_child(GTK_BIN(w)), ev);
    return TRUE;
}

GtkWidget *ui_frame(GtkWidget *child, double border, gboolean dashed)
{
    Frame *f = g_new0(Frame, 1);
    f->border = border;
    f->dashed = dashed;
    GtkWidget *w = gtk_event_box_new();
    g_object_set_data_full(G_OBJECT(w), "ek-frame", f, g_free);
    /* Pad with an alignment: an event box's window excludes its border width,
     * so border_width would leave the frame drawn outside the window. */
    GtkWidget *pad = gtk_alignment_new(0.5, 0.5, 1.0, 1.0);
    guint inset = ui_px(border + 16);
    gtk_alignment_set_padding(GTK_ALIGNMENT(pad), inset, inset, inset, inset);
    gtk_container_add(GTK_CONTAINER(pad), child);
    gtk_container_add(GTK_CONTAINER(w), pad);
    g_signal_connect(w, "expose-event", G_CALLBACK(frame_expose), f);
    return w;
}

GtkWidget *ui_screen(void)
{
    GtkWidget *box = gtk_vbox_new(FALSE, ui_px(18));
    gtk_container_set_border_width(GTK_CONTAINER(box), ui_px(UI_MARGIN));
    return box;
}

GtkWidget *ui_header(const char *title, UiClickFn back, gpointer data, GtkWidget **title_out,
                     GtkWidget **right_out)
{
    GtkWidget *vbox = gtk_vbox_new(FALSE, ui_px(12));
    GtkWidget *row = gtk_hbox_new(FALSE, ui_px(18));
    gtk_box_pack_start(GTK_BOX(vbox), row, FALSE, FALSE, 0);

    if (back) {
        GtkWidget *b = ui_button("\342\200\271 Back", UI_BTN_NORMAL, back, data);
        gtk_widget_set_size_request(b, ui_px(190), ui_px(UI_TOUCH - 12));
        gtk_box_pack_start(GTK_BOX(row), b, FALSE, FALSE, 0);
    }
    char *esc = g_markup_escape_text(title ? title : "", -1);
    GtkWidget *t = ui_label(esc, UI_FONT_UI, back ? 40 : UI_TITLE_PX, TRUE);
    g_free(esc);
    gtk_label_set_ellipsize(GTK_LABEL(t), PANGO_ELLIPSIZE_END);
    gtk_box_pack_start(GTK_BOX(row), t, TRUE, TRUE, 0);

    GtkWidget *right = gtk_hbox_new(FALSE, ui_px(12));
    gtk_box_pack_end(GTK_BOX(row), right, FALSE, FALSE, 0);

    gtk_box_pack_start(GTK_BOX(vbox), ui_rule(4), FALSE, FALSE, 0);
    if (title_out)
        *title_out = t;
    if (right_out)
        *right_out = right;
    return vbox;
}

GtkWidget *ui_message(const char *markup)
{
    GtkWidget *l = ui_label_wrapped(markup, UI_FONT_UI, UI_TEXT_PX, FALSE, 0);
    gtk_misc_set_alignment(GTK_MISC(l), 0.5, 0.5);
    gtk_label_set_justify(GTK_LABEL(l), GTK_JUSTIFY_CENTER);
    return l;
}

GtkWidget *ui_hbox_equal(void)
{
    return gtk_hbox_new(TRUE, ui_px(16));
}

void ui_clear(GtkWidget *container)
{
    GList *kids = gtk_container_get_children(GTK_CONTAINER(container));
    for (GList *k = kids; k; k = k->next)
        gtk_widget_destroy(k->data);
    g_list_free(kids);
}

/* --- Paged list ------------------------------------------------------------------ */

struct UiList {
    GtkWidget *rows_box;
    GtkWidget *nav;
    GtkWidget *prev, *next, *label;
    GPtrArray *rows;
    int per_page;
    int page;
};

static void list_free(gpointer data)
{
    UiList *l = data;
    g_ptr_array_free(l->rows, TRUE);
    g_free(l);
}

static void list_prev(GtkWidget *b, gpointer data)
{
    (void)b;
    UiList *l = data;
    ui_list_show_page(l, l->page - 1);
}

static void list_next(GtkWidget *b, gpointer data)
{
    (void)b;
    UiList *l = data;
    ui_list_show_page(l, l->page + 1);
}

GtkWidget *ui_list_new(int rows_per_page)
{
    UiList *l = g_new0(UiList, 1);
    l->rows = g_ptr_array_new();
    l->per_page = rows_per_page < 1 ? 1 : rows_per_page;

    GtkWidget *box = gtk_vbox_new(FALSE, ui_px(16));
    g_object_set_data_full(G_OBJECT(box), "ek-list", l, list_free);
    l->rows_box = gtk_vbox_new(FALSE, 0);
    gtk_box_pack_start(GTK_BOX(box), l->rows_box, FALSE, FALSE, 0);

    l->nav = gtk_hbox_new(FALSE, ui_px(16));
    gtk_widget_set_no_show_all(l->nav, TRUE);
    l->prev = ui_button("\342\200\271 Prev", UI_BTN_NORMAL, list_prev, l);
    l->next = ui_button("Next \342\200\272", UI_BTN_NORMAL, list_next, l);
    l->label = ui_label("", UI_FONT_UI, UI_SMALL_PX, FALSE);
    gtk_misc_set_alignment(GTK_MISC(l->label), 0.5, 0.5);
    gtk_widget_set_size_request(l->prev, ui_px(220), -1);
    gtk_widget_set_size_request(l->next, ui_px(220), -1);
    gtk_box_pack_start(GTK_BOX(l->nav), l->prev, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(l->nav), l->label, TRUE, TRUE, 0);
    gtk_box_pack_end(GTK_BOX(l->nav), l->next, FALSE, FALSE, 0);
    gtk_box_pack_end(GTK_BOX(box), l->nav, FALSE, FALSE, 0);
    return box;
}

UiList *ui_list_of(GtkWidget *list)
{
    return g_object_get_data(G_OBJECT(list), "ek-list");
}

void ui_list_add(UiList *l, GtkWidget *row)
{
    gtk_widget_set_no_show_all(row, TRUE);
    gtk_box_pack_start(GTK_BOX(l->rows_box), row, FALSE, FALSE, 0);
    g_ptr_array_add(l->rows, row);
    ui_list_show_page(l, l->page);
}

void ui_list_clear(UiList *l)
{
    ui_clear(l->rows_box);
    g_ptr_array_set_size(l->rows, 0);
    l->page = 0;
    ui_list_show_page(l, 0);
}

void ui_list_show_page(UiList *l, int page)
{
    int n = (int)l->rows->len;
    int pages = n ? (n + l->per_page - 1) / l->per_page : 1;
    if (page < 0 || page >= pages)
        page = page < 0 ? 0 : pages - 1;
    l->page = page;
    for (int i = 0; i < n; i++) {
        GtkWidget *row = g_ptr_array_index(l->rows, i);
        if (i / l->per_page == page) {
            gtk_widget_set_no_show_all(row, FALSE);
            gtk_widget_show_all(row);
        } else {
            gtk_widget_set_no_show_all(row, TRUE);
            gtk_widget_hide(row);
        }
    }
    if (pages > 1) {
        char *t = g_strdup_printf("Page %d of %d", page + 1, pages);
        gtk_label_set_text(GTK_LABEL(l->label), t);
        g_free(t);
        ui_button_set_disabled(l->prev, page == 0);
        ui_button_set_disabled(l->next, page == pages - 1);
        gtk_widget_set_no_show_all(l->nav, FALSE);
        gtk_widget_show_all(l->nav);
    } else {
        gtk_widget_set_no_show_all(l->nav, TRUE);
        gtk_widget_hide(l->nav);
    }
}

int ui_list_page(UiList *l)
{
    return l->page;
}

int ui_rows_fitting(double reserved_px, double row_px)
{
    double avail = ui_screen_h() / (ui_screen_w() / UI_DESIGN_W) - reserved_px;
    int n = (int)(avail / row_px);
    return n < 2 ? 2 : n;
}

/* --- Pager -------------------------------------------------------------------- */

struct Pager {
    GtkWidget *area;
    PangoFontDescription *font;
    PangoLayout *layout;
    char *text;
    gboolean markup;
    int laid_out_w;
    GArray *tops; /* y (px) where each page starts, plus a final sentinel */
    int page;
    gboolean select_mode;
    PagerFn on_change;
    gpointer change_data;
    PagerSelectFn on_select;
    gpointer select_data;
    double press_x, press_y;
    gboolean pressing;
    int notified_page, notified_pages;
};

static int pager_pad(void) { return ui_px(6); }
int pager_pages(Pager *p);

static void pager_free(gpointer data)
{
    Pager *p = data;
    if (p->layout)
        g_object_unref(p->layout);
    pango_font_description_free(p->font);
    g_array_free(p->tops, TRUE);
    g_free(p->text);
    g_free(p);
}

static void pager_notify(Pager *p)
{
    /* Relayout runs from size-allocate; only report real changes so a page
     * label update can't trigger an endless resize loop. */
    if (p->page == p->notified_page && pager_pages(p) == p->notified_pages)
        return;
    p->notified_page = p->page;
    p->notified_pages = pager_pages(p);
    if (p->on_change)
        p->on_change(p, p->change_data);
}

/* Splits the layout into pages at line boundaries for the current size. */
static void pager_relayout(Pager *p)
{
    GtkAllocation a;
    gtk_widget_get_allocation(p->area, &a);
    int w = a.width - 2 * pager_pad(), h = a.height - 2 * pager_pad();
    if (w <= 1 || h <= 1 || !p->text)
        return;

    if (!p->layout) {
        p->layout = gtk_widget_create_pango_layout(p->area, NULL);
        pango_layout_set_font_description(p->layout, p->font);
        pango_layout_set_wrap(p->layout, PANGO_WRAP_WORD_CHAR);
        pango_layout_set_spacing(p->layout, ui_px(8) * PANGO_SCALE);
    }
    if (p->markup)
        pango_layout_set_markup(p->layout, p->text, -1);
    else
        pango_layout_set_text(p->layout, p->text, -1);
    pango_layout_set_width(p->layout, w * PANGO_SCALE);
    p->laid_out_w = a.width;

    g_array_set_size(p->tops, 0);
    int top = 0;
    g_array_append_val(p->tops, top);
    PangoLayoutIter *it = pango_layout_get_iter(p->layout);
    do {
        PangoRectangle r;
        pango_layout_iter_get_line_extents(it, NULL, &r);
        int y0 = PANGO_PIXELS_FLOOR(r.y), y1 = PANGO_PIXELS_CEIL(r.y + r.height);
        if (y1 - top > h && y0 > top) {
            top = y0;
            g_array_append_val(p->tops, top);
        }
    } while (pango_layout_iter_next_line(it));
    pango_layout_iter_free(it);
    int end, unused;
    pango_layout_get_pixel_size(p->layout, &unused, &end);
    g_array_append_val(p->tops, end);

    int pages = (int)p->tops->len - 1;
    if (p->page >= pages)
        p->page = pages - 1;
    pager_notify(p);
}

static gboolean pager_expose(GtkWidget *w, GdkEventExpose *ev, gpointer data)
{
    Pager *p = data;
    GtkAllocation a;
    gtk_widget_get_allocation(w, &a);
    if (a.width != p->laid_out_w)
        pager_relayout(p);

    cairo_t *cr = gdk_cairo_create(gtk_widget_get_window(w));
    gdk_cairo_region(cr, ev->region);
    cairo_clip(cr);
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_paint(cr);
    if (p->layout && p->tops->len >= 2) {
        int top = g_array_index(p->tops, int, p->page);
        int bottom = g_array_index(p->tops, int, p->page + 1);
        cairo_rectangle(cr, 0, pager_pad(), a.width, bottom - top);
        cairo_clip(cr);
        cairo_set_source_rgb(cr, 0, 0, 0);
        cairo_move_to(cr, pager_pad(), pager_pad() - top);
        pango_cairo_show_layout(cr, p->layout);
    }
    cairo_destroy(cr);
    return TRUE;
}

static void pager_size_allocate(GtkWidget *w, GtkAllocation *a, gpointer data)
{
    (void)w;
    (void)a;
    pager_relayout(data);
}

static gboolean pager_press(GtkWidget *w, GdkEventButton *ev, gpointer data)
{
    (void)w;
    Pager *p = data;
    if (ev->button != 1)
        return FALSE;
    p->press_x = ev->x;
    p->press_y = ev->y;
    p->pressing = TRUE;
    return TRUE;
}

static gboolean pager_release(GtkWidget *w, GdkEventButton *ev, gpointer data)
{
    Pager *p = data;
    if (ev->button != 1 || !p->pressing)
        return FALSE;
    p->pressing = FALSE;

    GtkAllocation a;
    gtk_widget_get_allocation(w, &a);
    double dx = ev->x - p->press_x;
    if (fabs(dx) > ui_px(80) && fabs(dx) > fabs(ev->y - p->press_y)) {
        if (dx < 0)
            pager_next(p);
        else
            pager_prev(p);
        return TRUE;
    }
    if (p->select_mode && p->on_select && p->layout) {
        int top = g_array_index(p->tops, int, p->page);
        int index, trailing;
        int x = (int)ev->x - pager_pad(), y = (int)ev->y - pager_pad() + top;
        pango_layout_xy_to_index(p->layout, x * PANGO_SCALE, y * PANGO_SCALE, &index,
                                 &trailing);
        p->on_select(p, index, p->select_data);
        return TRUE;
    }
    /* Kindle convention: left third goes back, the rest goes forward. */
    if (ev->x < a.width / 3.0)
        pager_prev(p);
    else
        pager_next(p);
    return TRUE;
}

GtkWidget *pager_new(UiFace face, double px)
{
    Pager *p = g_new0(Pager, 1);
    p->font = ui_font(face, px, FALSE);
    p->tops = g_array_new(FALSE, FALSE, sizeof(int));
    p->area = gtk_drawing_area_new();
    g_object_set_data_full(G_OBJECT(p->area), "ek-pager", p, pager_free);
    gtk_widget_add_events(p->area, GDK_BUTTON_PRESS_MASK | GDK_BUTTON_RELEASE_MASK);
    g_signal_connect(p->area, "expose-event", G_CALLBACK(pager_expose), p);
    g_signal_connect(p->area, "size-allocate", G_CALLBACK(pager_size_allocate), p);
    g_signal_connect(p->area, "button-press-event", G_CALLBACK(pager_press), p);
    g_signal_connect(p->area, "button-release-event", G_CALLBACK(pager_release), p);
    return p->area;
}

Pager *pager_of(GtkWidget *area)
{
    return g_object_get_data(G_OBJECT(area), "ek-pager");
}

GtkWidget *pager_widget(Pager *p)
{
    return p->area;
}

void pager_set_text(Pager *p, const char *text, gboolean markup)
{
    g_free(p->text);
    p->text = g_strdup(text ? text : "");
    p->markup = markup;
    p->page = 0;
    p->laid_out_w = 0;
    p->notified_page = p->notified_pages = -1;
    pager_relayout(p);
    gtk_widget_queue_draw(p->area);
}

const char *pager_text(Pager *p)
{
    return p->layout ? pango_layout_get_text(p->layout) : "";
}

void pager_on_change(Pager *p, PagerFn fn, gpointer data)
{
    p->on_change = fn;
    p->change_data = data;
    p->notified_page = p->notified_pages = -1;
}

void pager_on_select(Pager *p, PagerSelectFn fn, gpointer data)
{
    p->on_select = fn;
    p->select_data = data;
}

void pager_set_select_mode(Pager *p, gboolean on)
{
    p->select_mode = on;
}

static gboolean pager_go(Pager *p, int page)
{
    if (page < 0 || page >= pager_pages(p) || page == p->page)
        return FALSE;
    p->page = page;
    gtk_widget_queue_draw(p->area);
    pager_notify(p);
    return TRUE;
}

gboolean pager_next(Pager *p) { return pager_go(p, p->page + 1); }
gboolean pager_prev(Pager *p) { return pager_go(p, p->page - 1); }
int pager_page(Pager *p) { return p->page; }
int pager_pages(Pager *p) { return p->tops->len >= 2 ? (int)p->tops->len - 1 : 1; }
