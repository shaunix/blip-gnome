# Copyright (c) 2006-2011  Shaun McCance  <shaunm@gnome.org>
#
# This file is part of Blip, a program for displaying various statistics
# of questionable relevance about software and the people who make it.
#
# Blip is free software; you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# Blip is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along
# with Blip; if not, write to the Free Software Foundation, 59 Temple Place,
# Suite 330, Boston, MA  0211-1307  USA.
#

import datetime
import re
import os

import blip.db
import blip.utils

import blip.parsers
import blip.parsers.automake

import blip.plugins.modules.sweep

class QuickRefScanner (blip.plugins.modules.sweep.ModuleFileScanner):
    def __init__ (self, scanner):
        self.document = None
        blip.plugins.modules.sweep.ModuleFileScanner.__init__ (self, scanner)
    
    def process_file (self, dirname, basename):
        """
        Process a Makefile.am file for the Evolution Quick Reference Card.
        """
        branch = self.scanner.branch
        is_quickref = False
        if branch.scm_server == 'git://git.gnome.org/' and branch.scm_module == 'evolution':
            if basename == 'Makefile.am':
                if os.path.join (self.scanner.repository.directory, 'help/quickref') == dirname:
                    is_quickref = True
        if not is_quickref:
            return

        filename = os.path.join (dirname, basename)
        rel_ch = blip.utils.relative_path (os.path.join (dirname, filename),
                                           self.scanner.repository.directory)
        bserver, bmodule, bbranch = branch.ident.split('/')[2:]

        with blip.db.Timestamp.stamped (filename, self.scanner.repository) as stamp:
            try:
                stamp.check (self.scanner.request.get_tool_option ('timestamps'))
            except:
                data = {'parent' : branch}
                scm_dir, scm_file = os.path.split (rel_ch)
                data['scm_dir'] = os.path.join (scm_dir, 'C')
                doc = blip.db.Branch.select_one (type=u'Document', **data)
                if doc is not None:
                    self.scanner.add_child (doc)
                    self.document = doc
                raise

            stamp.log ()
            makefile = blip.parsers.get_parsed_file (blip.parsers.automake.Automake,
                                                     self.scanner.branch, filename)

            ident = u'/'.join(['/doc', bserver, bmodule, 'quickref', bbranch])
            document = blip.db.Branch.get_or_create (ident, u'Document')
            document.parent = branch

            for key in ('scm_type', 'scm_server', 'scm_module', 'scm_branch', 'scm_path'):
                setattr (document, key, getattr (branch, key))
            document.subtype = u'evolutionquickref'
            document.scm_dir = blip.utils.relative_path (os.path.join (dirname, u'C'),
                                                         self.scanner.repository.directory)
            document.scm_file = u'quickref.tex'

            self.scanner.add_child (document)
            self.document = document

            translations = []
            for lang in makefile['SUBDIRS'].split():
                if lang == 'C':
                    continue
                lident = u'/l10n/' + lang + document.ident
                translation = blip.db.Branch.get_or_create (lident, u'Translation')
                translations.append (translation)
                for key in ('scm_type', 'scm_server', 'scm_module', 'scm_branch', 'scm_path'):
                    setattr (translation, key, getattr (document, key))
                translation.scm_dir = blip.utils.relative_path (os.path.join (dirname, lang),
                                                                self.scanner.repository.directory)
                translation.scm_file = u'quickref.tex'
                translation.parent = document
            document.set_children (u'Translation', translations)

    def post_process (self):
        if self.document is None:
            return
        regexp = re.compile ('\\s*\\\\textbf{\\\\Huge{(.*)}}')
        filename = os.path.join (self.scanner.repository.directory,
                                 self.document.scm_dir, self.document.scm_file)
        with blip.db.Timestamp.stamped (filename, self.scanner.repository) as stamp:
            stamp.check (self.scanner.request.get_tool_option ('timestamps'))
            stamp.log ()
            for line in open(filename):
                match = regexp.match (line)
                if match:
                    self.document.name = blip.utils.utf8dec (match.group(1))
                    break
        rev = blip.db.Revision.get_last_revision (branch=self.document.parent,
                                                  files=[os.path.join (self.document.scm_dir,
                                                                       self.document.scm_file)])
        if rev is not None:
            self.document.mod_datetime = rev.datetime
            self.document.mod_person = rev.person
        self.document.updated = datetime.datetime.utcnow ()

        for translation in blip.db.Branch.select (type=u'Translation', parent=self.document):
            rev = blip.db.Revision.get_last_revision (branch=self.document.parent,
                                                      files=[os.path.join (translation.scm_dir,
                                                                           translation.scm_file)])
            if rev is not None:
                translation.mod_datetime = rev.datetime
                translation.mod_person = rev.person
            translation.updated = datetime.datetime.utcnow()
