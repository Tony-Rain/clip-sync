#include <gtk/gtk.h>
#include <string.h>
#include <stdlib.h>

static gchar *gnome_data = NULL;
static gchar *uri_data = NULL;
static gchar *text_data = NULL;

static void get_func(GtkClipboard *cb, GtkSelectionData *sd, guint info, gpointer ud) {
    if (info == 0)
        gtk_selection_data_set(sd, gdk_atom_intern("x-special/gnome-copied-files", FALSE), 8,
                               (guchar*)gnome_data, strlen(gnome_data));
    else if (info == 1)
        gtk_selection_data_set(sd, gdk_atom_intern("text/uri-list", FALSE), 8,
                               (guchar*)uri_data, strlen(uri_data));
    else if (info == 2)
        gtk_selection_data_set_text(sd, text_data, -1);
}

static void clear_func(GtkClipboard *cb, gpointer ud) {
    gtk_main_quit();
}

int main(int argc, char *argv[]) {
    if (argc < 2) { fprintf(stderr, "Usage: %s <file>...\n", argv[0]); return 1; }

    setenv("GDK_BACKEND", "x11", 1);
    gtk_init(&argc, &argv);

    GString *gnome = g_string_new("copy");
    GString *uris = g_string_new("");
    GString *text = g_string_new("");

    for (int i = 1; i < argc; i++) {
        gchar *uri = g_filename_to_uri(argv[i], NULL, NULL);
        if (!uri) continue;
        g_string_append_printf(gnome, "\n%s", uri);
        g_string_append_printf(uris, "%s\r\n", uri);
        if (i > 1) g_string_append_c(text, '\n');
        g_string_append(text, argv[i]);
        g_free(uri);
    }

    gnome_data = g_string_free(gnome, FALSE);
    uri_data = g_string_free(uris, FALSE);
    text_data = g_string_free(text, FALSE);

    GtkTargetEntry targets[] = {
        {"x-special/gnome-copied-files", 0, 0},
        {"text/uri-list", 0, 1},
        {"text/plain", 0, 2},
    };

    GtkClipboard *cb = gtk_clipboard_get(GDK_SELECTION_CLIPBOARD);
    gtk_clipboard_set_with_data(cb, targets, 3, get_func, clear_func, NULL);

    g_timeout_add_seconds(300, (GSourceFunc)gtk_main_quit, NULL);
    gtk_main();

    g_free(gnome_data);
    g_free(uri_data);
    g_free(text_data);
    return 0;
}
