/* Test-only stub: simulate libxapp present but icon creation fails. */
void *xapp_status_icon_new_with_name(const char *name) { return 0; }
void xapp_status_icon_set_icon_name(void *icon, const char *name) {}
void xapp_status_icon_set_tooltip_text(void *icon, const char *text) {}
void xapp_status_icon_set_visible(void *icon, int visible) {}
int xapp_status_icon_get_state(void *icon) { return 2; }
