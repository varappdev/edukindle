#include <gtk/gtk.h>

/*
 * The Kindle window manager places windows based on a structured title:
 *   L:<layer>_N:<name>_PC:<chrome>_ID:<app id>
 * L:A = application layer. PC:N hides the system status bar, PC:T keeps it.
 * Change the ID to something unique to your app.
 */
#define KINDLE_WINDOW_TITLE "L:A_N:application_PC:T_ID:com.edukindle.app"

static void on_quit_clicked(GtkButton *button, gpointer user_data)
{
    (void)button;
    (void)user_data;
    gtk_main_quit();
}

int main(int argc, char *argv[])
{
    gtk_init(&argc, &argv);

    GtkWidget *window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window), KINDLE_WINDOW_TITLE);
    g_signal_connect(window, "destroy", G_CALLBACK(gtk_main_quit), NULL);

    GtkWidget *vbox = gtk_vbox_new(FALSE, 24);
    gtk_container_set_border_width(GTK_CONTAINER(vbox), 48);
    gtk_container_add(GTK_CONTAINER(window), vbox);

    GtkWidget *label = gtk_label_new(NULL);
    gtk_label_set_markup(GTK_LABEL(label),
                         "<span size='xx-large' weight='bold'>EduKindle</span>");
    gtk_box_pack_start(GTK_BOX(vbox), label, TRUE, TRUE, 0);

    GtkWidget *quit = gtk_button_new_with_label("Quit");
    g_signal_connect(quit, "clicked", G_CALLBACK(on_quit_clicked), NULL);
    gtk_box_pack_end(GTK_BOX(vbox), quit, FALSE, FALSE, 0);

    gtk_widget_show_all(window);
    gtk_main();
    return 0;
}
