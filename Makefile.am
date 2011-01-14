SUBDIRS =		\
	gnomeapp	\
	gnomedoc	\
	gnomescm	\
	intltool	\
	jhbuild

EXTRA_DIST = ChangeLog

ChangeLog:
	@if test -f $(top_srcdir)/.git/HEAD; then \
	  git log --stat > $@; \
	fi

dist: ChangeLog
.PHONY: ChangeLog
