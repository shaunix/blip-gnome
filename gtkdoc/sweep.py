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

import commands
import ConfigParser
import datetime
import re
import subprocess
import os
import urllib

import blinq.config

import blip.db
import blip.utils

import blip.parsers
import blip.parsers.automake

import blip.plugins.modules.sweep
from blip.plugins.gnomedoc.sweep import GnomeDocScanner

class GtkDocScanner (blip.plugins.modules.sweep.ModuleFileScanner):
    def __init__ (self, scanner):
        self.documents = []
        self.translations = []
        blip.plugins.modules.sweep.ModuleFileScanner.__init__ (self, scanner)
    
    def process_file (self, dirname, basename):
        if basename != 'Makefile.am':
            return

        filename = os.path.join (dirname, basename)
        rel_ch = blip.utils.relative_path (os.path.join (dirname, filename),
                                           self.scanner.repository.directory)
        branch = self.scanner.branch

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
                    self.documents.append (doc)
                    for translation in doc.select_children (u'Translation'):
                        self.translations.append (translation)
                raise

            makefile = blip.parsers.get_parsed_file (blip.parsers.automake.Automake,
                                                     self.scanner.branch, filename)

            is_gtk_doc = False
            if basename == 'Makefile.am':
                for line in makefile.get_lines():
                    if line.startswith ('include $(top_srcdir)/'):
                        if line.endswith ('gtk-doc.make'):
                            is_gtk_doc = True
                            break
            if not is_gtk_doc:
                return

            stamp.log ()

            if 'DOC_MODULE' in makefile:
                doc_id = makefile['DOC_MODULE']
                doc_type = u'gtkdoc'
            else:
                return
            if doc_id == '@PACKAGE_NAME@':
                doc_id = branch.data.get ('pkgname')
            if doc_id is None:
                return
            # Using just doc_id sometimes conflicts with the ident of user help.
            ident = u'/'.join(['/doc', bserver, bmodule, doc_id + u'-docs', bbranch])
            document = blip.db.Branch.get_or_create (ident, u'Document')
            document.parent = branch

            for key in ('scm_type', 'scm_server', 'scm_module', 'scm_branch', 'scm_path'):
                setattr (document, key, getattr (branch, key))
            document.subtype = doc_type
            document.scm_dir = blip.utils.relative_path (dirname,
                                                         self.scanner.repository.directory)

            scm_file = makefile['DOC_MAIN_SGML_FILE']
            if '$(DOC_MODULE)' in scm_file:
                scm_file = scm_file.replace ('$(DOC_MODULE)', doc_id)
            document.scm_file = scm_file

            self.scanner.add_child (document)
            self.documents.append (document)

    def post_process (self):
        for document in self.documents:
            with blip.db.Error.catch (document):
                GnomeDocScanner.process_docbook (document, self.scanner)
            document.updated = datetime.datetime.utcnow ()
