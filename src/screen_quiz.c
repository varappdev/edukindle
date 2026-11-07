/* 3-question comprehension check, hinted retries, and the feedback screen. */

#include <string.h>

#include "app.h"

#define N_OPTIONS 4

static void go_back(GtkWidget *b, gpointer data)
{
    (void)b;
    (void)data;
    app_pop();
}

static Json *find_by_id(Json *arr, const char *id)
{
    json_each(item, arr) {
        if (!g_strcmp0(json_str(item, "id", NULL), id))
            return item;
    }
    return NULL;
}

/* --- Quiz ------------------------------------------------------------------------ */

typedef struct {
    GtkWidget *screen;
    char *section_id;
    Json *quiz;        /* {quiz_id, section_title, questions: [...]} */
    Json *last_result; /* retry mode: the graded attempt with hints */
    GPtrArray *ask;    /* Json* questions being asked, borrowed from quiz */
    int *chosen;
    int cur;
    gboolean submitting;
    GtkWidget *title;
    GtkWidget *body;
    GtkWidget *prev_btn;
    GtkWidget *next_btn;
    GtkWidget *options[N_OPTIONS];
} Quiz;

static void quiz_free(gpointer data)
{
    Quiz *q = data;
    g_free(q->section_id);
    json_free(q->quiz);
    json_free(q->last_result);
    if (q->ask)
        g_ptr_array_free(q->ask, TRUE);
    g_free(q->chosen);
    g_free(q);
}

static void quiz_show(Quiz *q);

static void quiz_sync_nav(Quiz *q)
{
    int n = (int)q->ask->len;
    gboolean last = q->cur == n - 1;
    ui_button_set_markup(q->next_btn, q->submitting ? "Checking\342\200\246"
                                      : last         ? "Submit"
                                                     : "Next \342\200\272");
    ui_button_set_disabled(q->next_btn, q->chosen[q->cur] < 0 || q->submitting);
    ui_button_set_disabled(q->prev_btn, q->cur == 0 || q->submitting);
}

static void quiz_option(GtkWidget *b, gpointer data)
{
    Quiz *q = data;
    if (q->submitting)
        return;
    q->chosen[q->cur] = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(b), "ek-opt"));
    for (int i = 0; i < N_OPTIONS; i++)
        if (q->options[i])
            ui_button_set_selected(q->options[i], i == q->chosen[q->cur]);
    quiz_sync_nav(q);
}

static void quiz_submitted(NetResponse *resp, gpointer data)
{
    Quiz *q = data;
    if (app_check(resp)) {
        q->submitting = FALSE;
        quiz_sync_nav(q);
        return;
    }
    Json *quiz = q->quiz, *result = resp->json;
    q->quiz = NULL; /* ownership moves to the results screen */
    resp->json = NULL;
    app_replace(screen_results_new(quiz, result));
}

static void quiz_submit(Quiz *q)
{
    GString *body = g_string_new("{\"answers\":{");
    for (guint i = 0; i < q->ask->len; i++) {
        if (i)
            g_string_append_c(body, ',');
        json_append_string(body, json_str(g_ptr_array_index(q->ask, i), "id", ""));
        g_string_append_printf(body, ":%d", q->chosen[i]);
    }
    g_string_append(body, "}}");

    char *esc = net_escape(json_str(q->quiz, "quiz_id", ""));
    char *path = g_strdup_printf("/api/quiz/%s/answers", esc);
    q->submitting = TRUE;
    quiz_sync_nav(q);
    net_post(path, body->str, G_OBJECT(q->screen), quiz_submitted, q);
    g_free(path);
    g_free(esc);
    g_string_free(body, TRUE);
}

static void quiz_next(GtkWidget *b, gpointer data)
{
    (void)b;
    Quiz *q = data;
    if (q->chosen[q->cur] < 0)
        return;
    if (q->cur == (int)q->ask->len - 1) {
        quiz_submit(q);
        return;
    }
    q->cur++;
    quiz_show(q);
}

static void quiz_prev(GtkWidget *b, gpointer data)
{
    (void)b;
    Quiz *q = data;
    if (q->cur > 0) {
        q->cur--;
        quiz_show(q);
    }
}

static void quiz_show(Quiz *q)
{
    Json *qn = g_ptr_array_index(q->ask, q->cur);
    Json *prior = q->last_result
                      ? find_by_id(json_get(q->last_result, "results"), json_str(qn, "id", ""))
                      : NULL;
    ui_clear(q->body);

    char *count = g_strdup_printf("Question %d of %d%s", q->cur + 1, (int)q->ask->len,
                                  prior ? " \302\267 second try" : "");
    GtkWidget *counter = ui_label(count, UI_FONT_UI, UI_SMALL_PX, TRUE);
    g_free(count);
    gtk_box_pack_start(GTK_BOX(q->body), counter, FALSE, FALSE, 0);

    char *prompt = ui_esc(json_str(qn, "prompt", ""));
    gtk_box_pack_start(GTK_BOX(q->body),
                       ui_label_wrapped(prompt, UI_FONT_READ, UI_TEXT_PX + 2, FALSE, 0), FALSE,
                       FALSE, ui_px(10));
    g_free(prompt);

    int first_choice = -1;
    if (prior) {
        first_choice = json_int(prior, "chosen", -1);
        char *hint = g_markup_printf_escaped("<b>Hint</b>  %s", json_str(prior, "hint", ""));
        int inner = ui_content_w() - 2 * ui_px(3 + 16);
        GtkWidget *hl = ui_label_wrapped(hint, UI_FONT_UI, UI_SMALL_PX + 2, FALSE, inner);
        g_free(hint);
        gtk_box_pack_start(GTK_BOX(q->body), ui_frame(hl, 3, TRUE), FALSE, FALSE, 0);
    }

    static const char letters[N_OPTIONS] = {'A', 'B', 'C', 'D'};
    int i = 0;
    memset(q->options, 0, sizeof q->options);
    json_each(opt, json_get(qn, "options")) {
        if (i >= N_OPTIONS)
            break;
        char *m = g_markup_printf_escaped("<b>%c</b>   %s%s", letters[i], opt->str ? opt->str : "",
                                          i == first_choice ? "   (your first answer)" : "");
        GtkWidget *b = ui_button(m, UI_BTN_NORMAL, quiz_option, q);
        g_free(m);
        gtk_misc_set_alignment(GTK_MISC(gtk_bin_get_child(GTK_BIN(b))), 0.0, 0.5);
        gtk_label_set_justify(GTK_LABEL(gtk_bin_get_child(GTK_BIN(b))), GTK_JUSTIFY_LEFT);
        ui_button_set_font(b, UI_FONT_UI, UI_BUTTON_PX, FALSE);
        ui_button_set_wrap(b, 0);
        g_object_set_data(G_OBJECT(b), "ek-opt", GINT_TO_POINTER(i));
        ui_button_set_selected(b, q->chosen[q->cur] == i);
        if (i == first_choice)
            ui_button_set_disabled(b, TRUE);
        gtk_box_pack_start(GTK_BOX(q->body), b, FALSE, FALSE, 0);
        q->options[i++] = b;
    }
    gtk_widget_show_all(q->body);
    quiz_sync_nav(q);
}

static void quiz_start(Quiz *q)
{
    /* The nav buttons stay hidden while the quiz is loading. */
    gtk_widget_set_no_show_all(q->prev_btn, FALSE);
    gtk_widget_set_no_show_all(q->next_btn, FALSE);
    gtk_widget_show_all(q->prev_btn);
    gtk_widget_show_all(q->next_btn);
    q->chosen = g_new(int, q->ask->len);
    for (guint i = 0; i < q->ask->len; i++)
        q->chosen[i] = -1;
    if (q->ask->len == 0) {
        ui_clear(q->body);
        gtk_box_pack_start(GTK_BOX(q->body), ui_message("Nothing to retry."), TRUE, TRUE, 0);
        gtk_widget_show_all(q->body);
        return;
    }
    quiz_show(q);
}

static void quiz_loaded(NetResponse *resp, gpointer data)
{
    Quiz *q = data;
    if (resp->error) {
        ui_clear(q->body);
        char *m = g_markup_printf_escaped("<b>Couldn't prepare a check.</b>\n\n%s", resp->error);
        gtk_box_pack_start(GTK_BOX(q->body), ui_message(m), TRUE, TRUE, 0);
        g_free(m);
        gtk_widget_show_all(q->body);
        return;
    }
    q->quiz = resp->json;
    resp->json = NULL;
    ui_label_printf(q->title, "Check: %s", json_str(q->quiz, "section_title", ""));
    q->ask = g_ptr_array_new();
    json_each(qn, json_get(q->quiz, "questions"))
        g_ptr_array_add(q->ask, qn);
    quiz_start(q);
}

static Quiz *quiz_build(const char *section_id)
{
    Quiz *q = g_new0(Quiz, 1);
    q->section_id = g_strdup(section_id);
    q->screen = ui_screen();
    g_object_set_data_full(G_OBJECT(q->screen), "ek-state", q, quiz_free);

    gtk_box_pack_start(GTK_BOX(q->screen),
                       ui_header("Comprehension check", go_back, NULL, &q->title, NULL), FALSE,
                       FALSE, 0);
    q->body = gtk_vbox_new(FALSE, ui_px(16));
    gtk_box_pack_start(GTK_BOX(q->screen), q->body, TRUE, TRUE, 0);

    GtkWidget *nav = ui_hbox_equal();
    q->prev_btn = ui_button("\342\200\271 Previous", UI_BTN_NORMAL, quiz_prev, q);
    q->next_btn = ui_button("Next \342\200\272", UI_BTN_PRIMARY, quiz_next, q);
    gtk_widget_set_no_show_all(q->prev_btn, TRUE);
    gtk_widget_set_no_show_all(q->next_btn, TRUE);
    gtk_box_pack_start(GTK_BOX(nav), q->prev_btn, TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(nav), q->next_btn, TRUE, TRUE, 0);
    gtk_box_pack_end(GTK_BOX(q->screen), nav, FALSE, FALSE, 0);
    return q;
}

GtkWidget *screen_quiz_new(const char *section_id)
{
    Quiz *q = quiz_build(section_id);
    gtk_box_pack_start(GTK_BOX(q->body),
                       ui_message("<b>Preparing your 3-question check\342\200\246</b>\n\n"
                                  "Questions adapt to what you already know."),
                       TRUE, TRUE, 0);
    GString *body = g_string_new("{\"section_id\":");
    json_append_string(body, section_id);
    g_string_append_c(body, '}');
    net_post("/api/quiz", body->str, G_OBJECT(q->screen), quiz_loaded, q);
    g_string_free(body, TRUE);
    return q->screen;
}

GtkWidget *screen_quiz_retry_new(Json *quiz, Json *result)
{
    Quiz *q = quiz_build(json_str(result, "section_id", ""));
    q->quiz = quiz;
    q->last_result = result;
    ui_label_printf(q->title, "Retry: %s", json_str(quiz, "section_title", ""));
    q->ask = g_ptr_array_new();
    json_each(id, json_get(result, "retry")) {
        Json *qn = find_by_id(json_get(quiz, "questions"), id->str);
        if (qn)
            g_ptr_array_add(q->ask, qn);
    }
    quiz_start(q);
    return q->screen;
}

/* --- Results ----------------------------------------------------------------------- */

typedef struct {
    GtkWidget *screen;
    Json *quiz;
    Json *result;
    GtkWidget *page_label;
    Pager *pager;
} Results;

static void results_free(gpointer data)
{
    Results *r = data;
    json_free(r->quiz);
    json_free(r->result);
    g_free(r);
}

static void results_page_changed(Pager *p, gpointer data)
{
    Results *r = data;
    char *pg = g_strdup_printf("%d/%d", pager_page(p) + 1, pager_pages(p));
    gtk_label_set_text(GTK_LABEL(r->page_label), pg);
    g_free(pg);
}

static void results_retry(GtkWidget *b, gpointer data)
{
    (void)b;
    Results *r = data;
    Json *quiz = r->quiz, *result = r->result;
    r->quiz = r->result = NULL;
    app_replace(screen_quiz_retry_new(quiz, result));
}

static void results_new_check(GtkWidget *b, gpointer data)
{
    (void)b;
    Results *r = data;
    char *sid = g_strdup(json_str(r->result, "section_id", ""));
    app_replace(screen_quiz_new(sid));
    g_free(sid);
}

static void results_next(GtkWidget *b, gpointer data)
{
    (void)b;
    Results *r = data;
    Json *next = json_get(r->result, "next");
    char *target = g_strdup(json_str(next, "section_id", ""));
    gboolean same = !g_strcmp0(target, json_str(r->result, "section_id", ""));
    app_pop(); /* back to the reader of the section just tested */
    if (!same)
        app_replace(screen_reader_new(target));
    g_free(target);
}

static char *results_markup(Json *res)
{
    GString *m = g_string_new(NULL);
    int n = 0;
    json_each(r, json_get(res, "results")) {
        gboolean ok = json_bool(r, "correct", FALSE);
        gboolean hinted = json_bool(r, "hinted", FALSE);
        const char *verdict = ok ? (hinted ? "Correct, with a hint" : "Correct")
                                 : (json_bool(r, "can_retry", FALSE) ? "Not quite"
                                                                     : "Not this time");
        if (n++)
            g_string_append(m, "\n\n");
        char *s = g_markup_printf_escaped("<b>%s</b>\n<i>%s</i>\nYour answer: %s\n%s",
                                          verdict, json_str(r, "prompt", ""),
                                          json_str(r, "chosen_text", ""),
                                          json_str(r, "feedback", ""));
        g_string_append(m, s);
        g_free(s);
        const char *hint = json_str(r, "hint", NULL);
        if (hint) {
            s = g_markup_printf_escaped("\n<b>Hint:</b> %s", hint);
            g_string_append(m, s);
            g_free(s);
        }
        if (!ok && json_str(r, "answer_text", NULL)) {
            s = g_markup_printf_escaped("\n<b>Answer:</b> %s. %s", json_str(r, "answer_text", ""),
                                        json_str(r, "explanation", ""));
            g_string_append(m, s);
            g_free(s);
        }
    }

    Json *mastery = json_get(res, "mastery");
    if (json_len(mastery)) {
        g_string_append(m, "\n\n<b>Mastery</b>");
        json_each(c, mastery) {
            char *s = g_markup_printf_escaped(
                "\n\342\200\242 %s: %d%% to %d%%", json_str(c, "label", ""),
                (int)(json_num(c, "before", 0) * 100 + 0.5),
                (int)(json_num(c, "after", 0) * 100 + 0.5));
            g_string_append(m, s);
            g_free(s);
        }
    }

    json_each(g, json_get(res, "gaps")) {
        Json *roots = json_get(g, "root_causes");
        if (!json_len(roots))
            continue;
        char *s = g_markup_printf_escaped("\n\n<b>Knowledge gap:</b> %s builds on ",
                                          json_str(g, "label", ""));
        g_string_append(m, s);
        g_free(s);
        int i = 0;
        json_each(root, roots) {
            s = g_markup_printf_escaped("%s%s (%s)", i++ ? ", " : "", json_str(root, "label", ""),
                                        !g_strcmp0(json_str(root, "status", ""), "weak")
                                            ? "weak"
                                            : "not checked yet");
            g_string_append(m, s);
            g_free(s);
        }
        g_string_append_c(m, '.');
    }

    Json *next = json_get(res, "next");
    if (!json_is_null(next)) {
        char *s = g_markup_printf_escaped("\n\n<b>Curriculum Architect:</b> %s",
                                          json_str(next, "reason", ""));
        g_string_append(m, s);
        g_free(s);
    }
    return g_string_free(m, FALSE);
}

GtkWidget *screen_results_new(Json *quiz, Json *result)
{
    Results *r = g_new0(Results, 1);
    r->quiz = quiz;
    r->result = result;
    r->screen = ui_screen();
    g_object_set_data_full(G_OBJECT(r->screen), "ek-state", r, results_free);

    GtkWidget *right;
    char *title = g_strdup_printf("Results: %s", json_str(quiz, "section_title", ""));
    gtk_box_pack_start(GTK_BOX(r->screen), ui_header(title, go_back, NULL, NULL, &right), FALSE,
                       FALSE, 0);
    g_free(title);
    r->page_label = ui_label("", UI_FONT_UI, UI_SMALL_PX, TRUE);
    gtk_box_pack_end(GTK_BOX(right), r->page_label, FALSE, FALSE, 0);

    /* Score block: big first-attempt score beside the Educator's verdict. */
    GtkWidget *top = gtk_hbox_new(FALSE, ui_px(24));
    Json *score = json_get(result, "score");
    char *sc = json_is_null(score)
                   ? g_strdup("\342\200\223")
                   : g_strdup_printf("%d/%d", (int)score->num, json_int(result, "total", 3));
    GtkWidget *big = ui_label(sc, UI_FONT_UI, 96, TRUE);
    g_free(sc);
    gtk_misc_set_alignment(GTK_MISC(big), 0.5, 0.5);
    gtk_box_pack_start(GTK_BOX(top), ui_frame(big, 4, FALSE), FALSE, FALSE, 0);
    char *msg = ui_esc(json_str(result, "message", ""));
    GtkWidget *ml = ui_label_wrapped(msg, UI_FONT_UI, UI_TEXT_PX - 2, FALSE,
                                     ui_content_w() - ui_px(300));
    g_free(msg);
    gtk_box_pack_start(GTK_BOX(top), ml, TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(r->screen), top, FALSE, FALSE, 0);

    GtkWidget *area = pager_new(UI_FONT_UI, UI_SMALL_PX + 4);
    r->pager = pager_of(area);
    pager_on_change(r->pager, results_page_changed, r);
    char *markup = results_markup(result);
    pager_set_text(r->pager, markup, TRUE);
    g_free(markup);
    app_set_pager(r->screen, r->pager);
    gtk_box_pack_start(GTK_BOX(r->screen), area, TRUE, TRUE, 0);

    GtkWidget *actions = ui_hbox_equal();
    int retry = json_len(json_get(result, "retry"));
    if (retry) {
        char *t = g_strdup_printf("Retry %d missed", retry);
        gtk_box_pack_start(GTK_BOX(actions), ui_button(t, UI_BTN_NORMAL, results_retry, r), TRUE,
                           TRUE, 0);
        g_free(t);
    } else {
        gtk_box_pack_start(GTK_BOX(actions),
                           ui_button("New check", UI_BTN_NORMAL, results_new_check, r), TRUE, TRUE,
                           0);
    }
    Json *next = json_get(result, "next");
    if (!json_is_null(next)) {
        const char *action = json_str(next, "action", "");
        const char *verb = !strcmp(action, "advance")  ? "Next"
                           : !strcmp(action, "review") ? "Review"
                                                       : "Re-read";
        char *t = g_markup_printf_escaped("%s \342\200\272\n<span weight='normal' font_desc='%dpx'>%s</span>",
                                          verb, ui_px(UI_SMALL_PX), json_str(next, "title", ""));
        GtkWidget *b = ui_button(t, retry ? UI_BTN_NORMAL : UI_BTN_PRIMARY, results_next, r);
        g_free(t);
        gtk_label_set_ellipsize(GTK_LABEL(gtk_bin_get_child(GTK_BIN(b))), PANGO_ELLIPSIZE_END);
        gtk_box_pack_start(GTK_BOX(actions), b, TRUE, TRUE, 0);
    }
    gtk_box_pack_end(GTK_BOX(r->screen), actions, FALSE, FALSE, 0);
    return r->screen;
}
