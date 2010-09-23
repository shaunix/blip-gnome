AC_INIT([blip-gnome], [0.9], [])
AM_INIT_AUTOMAKE([1.9])

AC_CONFIG_MACRO_DIR([m4])

AM_PATH_PYTHON([2.3])

PKG_CHECK_MODULES(BLIP_GNOME,
[
	blip >= 0.9
])

AC_CHECK_PROG(DOT, dot, dot)
if test x"$DOT" = x; then
  AC_MSG_ERROR([dot not found])
fi

BLIP_PLUGINS_DIR="`$PKG_CONFIG --variable pluginsdir blip`"
AC_SUBST(BLIP_PLUGINS_DIR)

AC_CONFIG_FILES([
Makefile
gnomeapp/Makefile
gnomedoc/Makefile
intltool/Makefile
jhbuild/Makefile
])

AC_OUTPUT

echo "
blip-gnome-$VERSION:

	Plugins:    ${BLIP_PLUGINS_DIR}
"
