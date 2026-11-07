#ifndef EK_UI_H
#define EK_UI_H

#include <gtk/gtk.h>

#include "json.h"

/*
 * E-ink widget kit. Everything is drawn flat in black and white with Cairo,
 * bypassing the GTK theme engine, so no gradients, bevels or hover effects can
 * sneak in. Sizes are given in "design pixels" for a 1072px-wide Kindle
 * Paperwhite screen and scaled to the real screen by ui_px().
 */

#define UI_DESIGN_W 1072.0
#define UI_MARGIN 40      /* screen side margin */
#define UI_TOUCH 112      /* minimum touch target height (~9.5 mm at 300 ppi) */
#define UI_TEXT_PX 40     /* body text */
#define UI_BUTTON_PX 36   /* button labels */
#define UI_SMALL_PX 30
#define UI_TITLE_PX 50

typedef enum { UI_FONT_UI, UI_FONT_READ, UI_FONT_MONO } UiFace;

void ui_init(int screen_w, int screen_h);
int ui_px(double design_px);
int ui_screen_w(void);
int ui_screen_h(void);
/* Usable width inside the side margins. */
int ui_content_w(void);

PangoFontDescription *ui_font(UiFace face, double px, gboolean bold);
void ui_set_font(GtkWidget *w, UiFace face, double px, gboolean bold);
void ui_paint_background(GtkWidget *w);

/* Labels take Pango markup. wrap=TRUE wraps at `wrap_w` px (0 = content width). */
GtkWidget *ui_label(const char *markup, UiFace face, double px, gboolean bold);
GtkWidget *ui_label_wrapped(const char *markup, UiFace face, double px, gboolean bold,
                            int wrap_w);
void ui_label_set(GtkWidget *label, const char *markup);
/* g_markup_printf_escaped wrapper that also sets the label. */
void ui_label_printf(GtkWidget *label, const char *fmt, ...) G_GNUC_PRINTF(2, 3);

/* --- Buttons ------------------------------------------------------------ */

typedef enum {
    UI_BTN_NORMAL,  /* white, black border */
    UI_BTN_PRIMARY, /* solid black, white text */
    UI_BTN_ROW,     /* full-width list row, left-aligned, rule underneath */
    UI_BTN_QUIET,   /* text only, no border */
} UiButtonStyle;

typedef void (*UiClickFn)(GtkWidget *button, gpointer data);

GtkWidget *ui_button(const char *markup, UiButtonStyle style, UiClickFn fn, gpointer data);
void ui_button_set_markup(GtkWidget *b, const char *markup);
void ui_button_set_selected(GtkWidget *b, gboolean selected);
gboolean ui_button_get_selected(GtkWidget *b);
void ui_button_set_disabled(GtkWidget *b, gboolean disabled);
/* Wrap long labels (quiz options) at the given width in px (0 = content width). */
void ui_button_set_wrap(GtkWidget *b, int width);
void ui_button_set_font(GtkWidget *b, UiFace face, double px, gboolean bold);

/* --- Layout helpers ------------------------------------------------------ */

GtkWidget *ui_rule(double design_thickness);
GtkWidget *ui_frame(GtkWidget *child, double design_border, gboolean dashed);
GtkWidget *ui_screen(void); /* vbox with screen padding */
/* Header with optional back button; *title_out and *right_out may be NULL. */
GtkWidget *ui_header(const char *title, UiClickFn back, gpointer data, GtkWidget **title_out,
                     GtkWidget **right_out);
/* Big centred "working" message that fills the remaining space. */
GtkWidget *ui_message(const char *markup);
GtkWidget *ui_hbox_equal(void);
/* Destroys all children of a container. */
void ui_clear(GtkWidget *container);
/* Escaped markup helper: caller frees. */
#define ui_esc(s) g_markup_escape_text((s) ? (s) : "", -1)

/* --- Paged list: rows shown a page at a time, with prev/next controls ------- */

typedef struct UiList UiList;
GtkWidget *ui_list_new(int rows_per_page);
UiList *ui_list_of(GtkWidget *list);
void ui_list_add(UiList *l, GtkWidget *row);
void ui_list_clear(UiList *l);
void ui_list_show_page(UiList *l, int page);
int ui_list_page(UiList *l);
/* Rows of `row_px` that fit after reserving `reserved_px` (design px). */
int ui_rows_fitting(double reserved_px, double row_px);

/* --- Pager: paginated rich text, the E-ink alternative to scrolling ---------- */

typedef struct Pager Pager;
typedef void (*PagerFn)(Pager *p, gpointer data);
typedef void (*PagerSelectFn)(Pager *p, int paragraph, gpointer data);

GtkWidget *pager_new(UiFace face, double px);
Pager *pager_of(GtkWidget *area);
GtkWidget *pager_widget(Pager *p);
/* Plain text; paragraphs are separated by blank lines. */
void pager_set_text(Pager *p, const char *text);
/* A server document: {"px": n, "blocks": [{"t": "p"|"h1"..|"li"|"note"|"code"|
 * "math"|"hr", "m": markup, ...}]}. The pager copies what it needs. */
void pager_set_doc(Pager *p, Json *doc);
void pager_on_change(Pager *p, PagerFn fn, gpointer data);
/* In select mode a tap reports the tapped block's source paragraph instead of
 * turning the page. */
void pager_on_select(Pager *p, PagerSelectFn fn, gpointer data);
void pager_set_select_mode(Pager *p, gboolean on);
gboolean pager_next(Pager *p);
gboolean pager_prev(Pager *p);
int pager_page(Pager *p);
int pager_pages(Pager *p);

#endif
