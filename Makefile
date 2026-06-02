PREFIX ?= /usr/local
BINDIR = $(PREFIX)/bin

GTK_CFLAGS = $(shell pkg-config --cflags gtk+-3.0)
GTK_LIBS = $(shell pkg-config --libs gtk+-3.0)

all: clip-set-files

clip-set-files: clip-set-files.c
	gcc -O2 -o $@ $< $(GTK_CFLAGS) $(GTK_LIBS) -Wall

install: clip-set-files
	install -d $(DESTDIR)$(BINDIR)
	install -m 755 clip-set-files $(DESTDIR)$(BINDIR)/
	install -m 755 clip-sync.py $(DESTDIR)$(BINDIR)/clip-sync

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/clip-sync
	rm -f $(DESTDIR)$(BINDIR)/clip-set-files

clean:
	rm -f clip-set-files

.PHONY: all install uninstall clean
