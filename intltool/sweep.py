# Copyright (c) 2006-2009  Shaun McCance  <shaunm@gnome.org>
#
# This file is part of Pulse, a program for displaying various statistics
# of questionable relevance about software and the people who make it.
#
# Pulse is free software; you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# Pulse is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along
# with Pulse; if not, write to the Free Software Foundation, 59 Temple Place,
# Suite 330, Boston, MA  0211-1307  USA.
#

import commands
import datetime
import re
import os
import shutil

try:
    from hashlib import md5
except:
    from md5 import new as md5

import blinq.config

import blip.parsers
import blip.parsers.po
import blip.parsers.autoconf
import blip.parsers.automake

import blip.plugins.modules.sweep

class IntltoolScanner (blip.plugins.modules.sweep.ModuleFileScanner):
    def __init__ (self, scanner):
        self.podirs = []
        self.gettet_package = None
        blip.plugins.modules.sweep.ModuleFileScanner.__init__ (self, scanner)
    
    def process_file (self, dirname, basename):
        if (dirname == self.scanner.repository.directory and
            basename in ('configure.ac', 'configure.in')):
            filename = os.path.join (dirname, basename)
            with blip.db.Timestamp.stamped (filename, self.scanner.repository) as stamp:
                stamp.check (self.scanner.request.get_tool_option ('timestamps'))
                stamp.log ()
                autoconf = blip.parsers.get_parsed_file (blip.parsers.autoconf.Autoconf,
                                                         self.scanner.branch, filename)
                self.gettext_package = autoconf.get_variable ('GETTEXT_PACKAGE')
        elif basename == 'POTFILES.in':
            self.podirs.append (dirname)

    def post_process (self):
        for dirname in self.podirs:
            self.post_process_dir (dirname)

    def post_process_dir (self, dirname):
        bserver, bmodule, bbranch = self.scanner.branch.ident.split('/')[2:]

        gettext_package = self.gettext_package
        filename = os.path.join (dirname, 'Makefile.in.in')
        if os.path.exists (filename):
            makefile = blip.parsers.get_parsed_file (blip.parsers.automake.Automake,
                                                     self.scanner.branch, filename)
            if makefile.has_key ('GETTEXT_PACKAGE'):
                gettext_package = makefile['GETTEXT_PACKAGE'].replace ('@GETTEXT_PACKAGE@',
                                                                       self.gettext_package)

        if self.gettext_package is None:
            blip.utils.warn ('Could not determine gettext package for %s' % self.scanner.branch.ident)
            return

        ident = u'/'.join(['/i18n', bserver, bmodule, gettext_package, bbranch])

        domain = blip.db.Branch.get_or_create (ident, u'Domain')
        domain.parent = self.scanner.branch
        self.scanner.add_child (domain)

        for key in ('scm_type', 'scm_server', 'scm_module', 'scm_branch', 'scm_path'):
            setattr (domain, key, getattr (self.scanner.branch, key))
        domain.scm_dir = blip.utils.relative_path (dirname,
                                                   self.scanner.repository.directory)

        linguas = os.path.join (dirname, 'LINGUAS')
        if not os.path.isfile (linguas):
            blip.db.Error.set_error (domain.ident,
                                     blip.utils.gettext ('No LINGUAS file'))
            return
        blip.db.Error.clear_error (domain.ident)

        filename = os.path.join (dirname, 'POTFILES.in')
        translations = []
        with blip.db.Timestamp.stamped (filename, self.scanner.repository) as stamp:
            try:
                stamp.check (self.scanner.request.get_tool_option ('timestamps'))
            except:
                translations = list(blip.db.Branch.select_children (domain, u'Translation'))
                raise
            stamp.log ()

            langs = []
            fd = open (linguas)
            for line in fd:
                if line.startswith ('#') or line == '\n':
                    continue
                for lang in line.split ():
                    langs.append (lang)
            for lang in langs:
                lident = u'/l10n/' + lang + domain.ident
                translation = blip.db.Branch.get_or_create (lident, u'Translation')
                translations.append (translation)
                for key in ('scm_type', 'scm_server', 'scm_module', 'scm_branch', 'scm_path', 'scm_dir'):
                    setattr (translation, key, getattr (domain, key))
                translation.subtype = u'intltool'
                translation.scm_file = lang + u'.po'
                translation.parent = domain
            domain.set_children (u'Translation', translations)

        for translation in translations:
            self.update_translation (translation)

    def update_translation (self, translation):
        # FIXME: We regenerate potfile even if no translations are updated,
        # because changes in various source files could affect it. Can we
        # somehow skip this when unnecessary? Possibly checking the repo
        # revision number?
        potfile = IntltoolScanner.get_potfile (translation, self.scanner)
        if potfile is None:
            return

        filename = os.path.join (self.scanner.repository.directory,
                                 translation.scm_dir,
                                 translation.scm_file)
        if not os.path.exists (filename):
            blip.db.Error.set_error (translation.ident,
                                     blip.utils.gettext ('File %s does not exist') %
                                     translation.scm_file)
            return

        with blip.db.Timestamp.stamped (filename, self.scanner.repository) as stamp:
            stamp.check (self.scanner.request.get_tool_option ('timestamps'))

            podir = os.path.join (self.scanner.repository.directory,
                                  translation.scm_dir)
            cmd = 'msgmerge "%s" "%s" 2>&1' % (translation.scm_file, potfile.get_file_path ())
            owd = os.getcwd ()
            try:
                os.chdir (podir)
                stamp.log ()
                popo = blip.parsers.po (self.scanner.branch, os.popen (cmd))
                stats = popo.get_stats ()
                total = stats[0] + stats[1] + stats[2]
                blip.db.Statistic.set_statistic (translation,
                                                 blip.utils.daynum (),
                                                 u'Messages',
                                                 stats[0], stats[1], total)
            finally:
                os.chdir (owd)

            of = blip.db.OutputFile.select_one (type=u'l10n',
                                                ident=translation.parent.ident,
                                                filename=translation.scm_file)
            if of is None:
                of = blip.db.OutputFile (type=u'l10n',
                                         ident=translation.parent.ident,
                                         filename=translation.scm_file,
                                         datetime=datetime.datetime.utcnow ())
            outfile_abs = of.get_file_path ()
            outfile_rel = blip.utils.relative_path (outfile_abs,
                                                    os.path.join (blinq.config.web_files_dir, 'l10n'))
            outdir = os.path.dirname (outfile_abs)
            if not os.path.exists (outdir):
                os.makedirs (outdir)
            blip.utils.log ('Copying PO file %s' % outfile_rel)
            shutil.copyfile (os.path.join (self.scanner.repository.directory,
                                           translation.scm_dir,
                                           translation.scm_file),
                             outfile_abs)
            of.datetime = datetime.datetime.utcnow ()
            of.data['revision'] = self.scanner.repository.get_revision ()

            files = [os.path.join (translation.scm_dir, translation.scm_file)]
            revision = blip.db.Revision.get_last_revision (branch=translation.parent.parent,
                                                           files=files)
            if revision is not None:
                translation.mod_datetime = revision.datetime
                translation.mod_person = revision.person

            translation.data['md5'] = potfile.data.get ('md5', None)

    potfiles = {}
    @classmethod
    def get_potfile (cls, translation, scanner):
        domain = translation.parent
        indir = os.path.dirname (os.path.join (scanner.repository.directory,
                                               domain.scm_dir))
        if cls.potfiles.has_key (indir):
            return cls.potfiles[indir]

        if domain.scm_dir == u'po':
            potname = domain.scm_module
        else:
            potname = domain.scm_dir
        potfile = potname + u'.pot'

        of = blip.db.OutputFile.select_one (type=u'l10n', ident=domain.ident, filename=potfile)
        if of is None:
            of = blip.db.OutputFile (type=u'l10n', ident=domain.ident, filename=potfile,
                                     datetime=datetime.datetime.utcnow ())
        potfile_abs = of.get_file_path ()
        potfile_rel = blip.utils.relative_path (potfile_abs,
                                                os.path.join (blinq.config.web_files_dir, 'l10n'))

        if not scanner.request.get_tool_option ('timestamps'):
            dt = of.data.get ('mod_datetime')
            if dt is not None and dt == domain.parent.mod_datetime:
                cls.potfiles[indir] = of
                return of

        potdir = os.path.dirname (potfile_abs)
        if not os.path.exists (potdir):
            os.makedirs (potdir)

        cmd = 'intltool-update -p -g "%s" && mv "%s" "%s"' % (potname, potfile, potdir)
        owd = os.getcwd ()
        try:
            os.chdir (indir)
            blip.utils.log ('Creating POT file %s' % potfile_rel)
            (mstatus, moutput) = commands.getstatusoutput (
                'rm -f missing notexist && intltool-update -m')
            (status, output) = commands.getstatusoutput (cmd)
        finally:
            os.chdir (owd)

        missing = []
        if mstatus == 0:
            mfile = os.path.join (indir, 'missing')
            if os.access (mfile, os.R_OK):
                missing = [line.strip() for line in open(mfile).readlines()]

        if status == 0:
            potmd5 = md5 ()
            # We don't start feeding potmd5 until we've hit a blank line.
            # This keeps inconsequential differences in the header from
            # affecting the MD5.
            blanklink = False
            popo = blip.parsers.po.Po (scanner.branch)
            for line in open (potfile_abs):
                if blanklink:
                    potmd5.update (line)
                elif line.strip() == '':
                    blankline = True
                popo.feed (line)
            popo.finish ()
            num = popo.get_num_messages ()
            of.datetime = datetime.datetime.utcnow ()
            of.data['mod_datetime'] = domain.parent.mod_datetime
            of.data['missing'] = missing
            of.statistic = num
            of.data['md5'] = potmd5.hexdigest ()
            cls.potfiles[indir] = of
            translation.parent.updated = of.datetime
            return of
        else:
            # FIXME: set_error
            blip.utils.warn ('Failed to create POT file %s' % potfile_rel)
            cls.potfiles[indir] = None
            return None
