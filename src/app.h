#ifndef EK_APP_H
#define EK_APP_H

#include <gtk/gtk.h>

#include "json.h"
#include "net.h"
#include "ui.h"

/*
 * Screens form a stack. Each screen is a widget that owns its own state (via
 * g_object_set_data_full) and is destroyed when popped.
 */
void app_push(GtkWidget *screen);
void app_pop(void);
/* Pops the top screen and pushes `screen` in its place. */
void app_replace(GtkWidget *screen);
/* Called whenever `screen` becomes the top screen again after a pop. */
void app_on_resume(GtkWidget *screen, void (*fn)(gpointer data), gpointer data);
/* Hardware page-turn keys and arrows go to this pager while `screen` is on top. */
void app_set_pager(GtkWidget *screen, Pager *pager);

void app_toast(const char *fmt, ...) G_GNUC_PRINTF(1, 2);
/* Remembers a working server address in edukindle.conf. */
void app_save_server(const char *label);
void app_quit(void);

/* Shown where a page needs a server feature an older server lacks. */
#define APP_OLD_SERVER                                                                     \
    "This page needs a newer EduKindle server. Restart the server on your computer to " \
    "update it."

/* Shows a server error as a toast; returns TRUE if there was one. */
gboolean app_check(NetResponse *resp);

/* Screens */
/* startup: first screen; auto-connects to the saved server, then becomes Home. */
GtkWidget *screen_connect_new(gboolean startup);
GtkWidget *screen_home_new(void);
GtkWidget *screen_toc_new(const char *doc_id);
GtkWidget *screen_reader_new(const char *section_id);
/* mode: summary | simple | paragraph | concept */
GtkWidget *screen_explain_new(const char *section_id, const char *mode, int paragraph,
                              const char *concept_id, const char *concept_label);
GtkWidget *screen_quiz_new(const char *section_id);
/* Retry the missed questions of a graded quiz; takes ownership of both trees. */
GtkWidget *screen_quiz_retry_new(Json *quiz, Json *result);
/* Takes ownership of quiz and result. */
GtkWidget *screen_results_new(Json *quiz, Json *result);
GtkWidget *screen_progress_new(const char *doc_id);
GtkWidget *screen_map_new(const char *doc_id);

#endif
