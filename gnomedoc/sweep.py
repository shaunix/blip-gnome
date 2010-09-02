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
import ConfigParser
import datetime
import re
import os
import urllib

try:
    from hashlib import md5
except:
    from md5 import new as md5

import libxml2

import blinq.config

import blip.data
import blip.db
import blip.utils

import blip.parsers
import blip.parsers.automake
import blip.parsers.po

import blip.plugins.modules.sweep

class GnomeDocScanner (blip.plugins.modules.sweep.ModuleFileScanner):
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
                                                     filename)

            is_gdu_doc = False
            for line in makefile.get_lines ():
                if line.startswith ('include $(top_srcdir)/'):
                    if line.endswith ('gnome-doc-utils.make'):
                        is_gdu_doc = True
                        break
            if not is_gdu_doc:
                return

            if not 'DOC_MODULE' in makefile:
                blip.utils.warn ('DOC_MODULE missing in %s' % rel_ch)
                return
            doc_module = makefile['DOC_MODULE']
            if doc_module == '@PACKAGE_NAME@':
                doc_module = branch.data.get ('PACKAGE_NAME', '@PACKAGE_NAME@')
            ident = u'/'.join(['/doc', bserver, bmodule, doc_module, bbranch])
            document = blip.db.Branch.get_or_create (ident, u'Document')
            document.parent = branch

            for key in ('scm_type', 'scm_server', 'scm_module', 'scm_branch', 'scm_path'):
                setattr (document, key, getattr (branch, key))
            document.subtype = u'gdu-docbook'
            document.scm_dir = blip.utils.relative_path (os.path.join (dirname, 'C'),
                                                         self.scanner.repository.directory)
            document.scm_file = doc_module + '.xml'

            fnames = ([doc_module + '.xml'] +
                      makefile.get('DOC_INCLUDES', '').split() +
                      makefile.get('DOC_ENTITIES', '').split() )
            xmlfiles = sorted (fnames)
            document.data['scm_files'] = xmlfiles

            files = [os.path.join (document.scm_dir, f) for f in xmlfiles]
            if len(files) == 0:
                document.mod_score = 0
            else:
                #FIXME
                pass

            translations = []
            if makefile.has_key ('DOC_LINGUAS'):
                for lang in makefile['DOC_LINGUAS'].split():
                    lident = u'/l10n/' + lang + document.ident
                    translation = blip.db.Branch.get_or_create (lident, u'Translation')
                    translations.append (translation)
                    for key in ('scm_type', 'scm_server', 'scm_module', 'scm_branch', 'scm_path'):
                        setattr (translation, key, getattr (document, key))
                    translation.subtype = u'xml2po'
                    translation.scm_dir = blip.utils.relative_path (os.path.join (dirname, lang),
                                                                    self.scanner.repository.directory)
                    translation.scm_file = lang + '.po'
                    translation.parent = document
                document.set_children (u'Translation', translations)

            self.documents.append (document)
            for translation in translations:
                self.translations.append (translation)

    def post_process (self):
        for document in self.documents:
            if document.subtype == u'gdu-docbook':
                GnomeDocScanner.process_docbook (document, self.scanner)
            rev = blip.db.Revision.get_last_revision (branch=document.parent,
                                                      files=[os.path.join (document.scm_dir, fname)
                                                             for fname in document.data.get ('scm_files', [])])
            if rev is not None:
                document.mod_datetime = rev.datetime
                document.mod_person = rev.person
            document.updated = datetime.datetime.utcnow ()
        for translation in self.translations:
            if translation.subtype == u'xml2po':
                GnomeDocScanner.process_xml2po (translation, self.scanner)

    @classmethod
    def process_docbook (cls, document, scanner):
        filename = os.path.join (scanner.repository.directory,
                                 document.scm_dir, document.scm_file)
        rel_ch = blip.utils.relative_path (filename,
                                           scanner.repository.directory)
        blip.utils.log ('Processing file %s' % rel_ch)

        title = None
        abstract = None
        credits = []
        try:
            ctxt = libxml2.newParserCtxt ()
            xmldoc = ctxt.ctxtReadFile (filename, None, 0)
            xmldoc.xincludeProcess ()
            root = xmldoc.getRootElement ()
        except Exception, e:
            blip.db.Error.set_error (document.ident, unicode (e))
            return
        blip.db.Error.clear_error (document.ident)
        seen = 0
        document.data['status'] = '00none'
        for node in blip.data.xmliter (root):
            if node.type != 'element':
                continue
            if node.name[-4:] == 'info':
                seen += 1
                infonodes = list (blip.data.xmliter (node))
                i = 0
                while i < len(infonodes):
                    infonode = infonodes[i]
                    if infonode.type != 'element':
                        i += 1
                        continue
                    if infonode.name == 'title':
                        if title is None:
                            title = infonode.getContent ()
                    elif infonode.name == 'abstract' and infonode.prop('role') == 'description':
                        abstract = infonode.getContent ()
                    elif infonode.name == 'releaseinfo':
                        if infonode.prop ('revision') == document.parent.data.get ('series'):
                            document.data['status'] = infonode.prop ('role')
                    elif infonode.name == 'authorgroup':
                        infonodes.extend (list (blip.data.xmliter (infonode)))
                    elif infonode.name in ('author', 'editor', 'othercredit'):
                        cr_name, cr_email = personname (infonode)
                        maint = (infonode.prop ('role') == 'maintainer')
                        credits.append ((cr_name, cr_email, infonode.name, maint))
                    elif infonode.name == 'collab':
                        cr_name = None
                        for ch in blip.data.xmliter (infonode):
                            if ch.type == 'element' and ch.name == 'collabname':
                                cr_name = normalize (ch.getContent ())
                        if cr_name is not None:
                            maint = (infonode.prop ('role') == 'maintainer')
                            credits.append ((cr_name, None, 'collab', maint))
                    elif infonode.name in ('corpauthor', 'corpcredit'):
                        maint = (infonode.prop ('role') == 'maintainer')
                        credits.append ((normalize (infonode.getContent ()),
                                              None, infonode.name, maint))
                    elif infonode.name == 'publisher':
                        cr_name = None
                        for ch in blip.data.xmliter (infonode):
                            if ch.type == 'element' and ch.name == 'publishername':
                                cr_name = normalize (ch.getContent ())
                        if cr_name is not None:
                            maint = (infonode.prop ('role') == 'maintainer')
                            credits.append ((cr_name, None, 'publisher', maint))
                    i += 1
            elif node.name == 'title':
                seen += 1
                title = node.getContent ()
            if seen > 1:
                break

        if title is not None:
            document.name = unicode (normalize (title))
        if abstract is not None:
            document.desc = unicode (normalize (abstract))

        rels = []
        for cr_name, cr_email, cr_type, cr_maint in credits:
            ent = None
            if cr_email is not None:
                ent = blip.db.Entity.get_or_create_email (cr_email)
            if ent is None:
                ident = u'/ghost/' + urllib.quote (cr_name)
                ent = blip.db.Entity.get_or_create (ident, u'Ghost')
                if ent.ident == ident:
                    ent.name = blip.utils.utf8dec (cr_name)
            if ent is not None:
                ent.extend (name=blip.utils.utf8dec (cr_name))
                ent.extend (email=blip.utils.utf8dec (cr_email))
                rel = blip.db.DocumentEntity.set_related (document, ent)
                if cr_type in ('author', 'corpauthor'):
                    rel.author = True
                elif cr_type == 'editor':
                    rel.editor = True
                elif cr_type == 'publisher':
                    rel.publisher = True
                if cr_maint:
                    rel.maintainer = True
                rels.append (rel)
        document.set_relations (blip.db.DocumentEntity, rels)

    @classmethod
    def process_xml2po (cls, translation, scanner):
        filename = os.path.join (scanner.repository.directory,
                                 translation.scm_dir, translation.scm_file)
        rel_ch = blip.utils.relative_path (filename,
                                           scanner.repository.directory)
        blip.utils.log ('Processing file %s' % rel_ch)

        potfile = cls.get_potfile (translation, scanner)
        if potfile is None:
            return None

        filepath = os.path.join (scanner.repository.directory,
                                 translation.scm_dir,
                                 translation.scm_file)
        if not os.path.exists (filepath):
            # FIXME: set_error
            blip.utils.warn ('Could not location file %s for %s' %
                             (translation.scm_file, translation.parent.ident))
            return
        with blip.db.Timestamp.stamped (filepath, scanner.repository) as stamp:
            try:
                stamp.check (scanner.request.get_tool_option ('timestamps'))
            except:
                # If the checksums differ, ignore the timestamp
                pomd5 = translation.data.get ('md5', None)
                potmd5 = potfile.data.get ('md5', None)
                if pomd5 is not None and pomd5 == potmd5:
                    raise
            makedir = os.path.join (scanner.repository.directory,
                                    os.path.dirname (translation.scm_dir))
            cmd = 'msgmerge "%s" "%s" 2>&1' % (
                os.path.join (os.path.basename (translation.scm_dir), translation.scm_file),
                potfile.get_file_path ())
            owd = os.getcwd ()
            try:
                os.chdir (makedir)
                pofile = blip.parsers.po.Po (os.popen (cmd))
                stats = pofile.get_stats ()
                total = stats[0] + stats[1] + stats[2]
                blip.db.Statistic.set_statistic (translation,
                                                 blip.utils.daynum (),
                                                 u'Messages',
                                                 stats[0], stats[1], total)
                stats = pofile.get_image_stats ()
                total = stats[0] + stats[1] + stats[2]
                blip.db.Statistic.set_statistic (translation,
                                                 blip.utils.daynum (),
                                                 u'ImageMessages',
                                                 stats[0], stats[1], stats[2])
            finally:
                os.chdir (owd)

    potfiles = {}
    @classmethod
    def get_potfile (cls, translation, scanner):
        domain = translation.parent
        indir = os.path.dirname (os.path.join (scanner.repository.directory,
                                               domain.scm_dir))
        if cls.potfiles.has_key (indir):
            return cls.potfiles[indir]

        makefile = blip.parsers.get_parsed_file (blip.parsers.automake.Automake,
                                                 os.path.join (indir, 'Makefile.am'))
        doc_module = makefile['DOC_MODULE']
        if doc_module == '@PACKAGE_NAME@':
            doc_module = domain.parent.data.get ('PACKAGE_NAME', '@PACKAGE_NAME@')
        docfiles = [os.path.join ('C', fname)
                    for fname
                    in ([doc_module+'.xml'] +
                        makefile.get('DOC_INCLUDES', '').split() +
                        makefile.get('DOC_PAGES', '').split()
                        )]
        potname = doc_module
        potfile = potname + u'.pot'
        of = blip.db.OutputFile.select_one (type=u'l10n', ident=domain.ident, filename=potfile)
        if of is None:
            of = blip.db.OutputFile (type=u'l10n', ident=domain.ident, filename=potfile,
                                     datetime=datetime.datetime.now())
        potfile_abs = of.get_file_path ()
        potfile_rel = blip.utils.relative_path (potfile_abs,
                                                os.path.join (blinq.config.web_files_dir, 'l10n'))

        if not scanner.request.get_tool_option ('timestamps'):
            dt = of.data.get ('mod_datetime')
            if dt is not None and dt == domain.parent.mod_datetime:
                blip.utils.log ('Skipping POT file %s' % potfile_rel)
                cls.potfiles[indir] = of
                return of

        potdir = os.path.dirname (potfile_abs)
        if not os.path.exists (potdir):
            os.makedirs (potdir)

        cmd = 'xml2po -e -o "' + potfile_abs + '" "' + '" "'.join(docfiles) + '"'
        owd = os.getcwd ()
        try:
            os.chdir (indir)
            blip.utils.log ('Creating POT file %s' % potfile_rel)
            (status, output) = commands.getstatusoutput (cmd)
        finally:
            os.chdir (owd)
        if status == 0:
            potmd5 = md5 ()
            # We don't start feeding potmd5 until we've hit a blank line.
            # This keeps inconsequential differences in the header from
            # affecting the MD5.
            blanklink = False
            popo = blip.parsers.po.Po ()
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
            of.statistic = num
            of.data['md5'] = potmd5.hexdigest ()
            cls.potfiles[indir] = of
            return of
        else:
            # FIXME: set_error
            blip.utils.warn ('Failed to create POT file %s' % potfile_rel)
            cls.potfiles[indir] = None
            return None

def normalize (string):
    if string is None:
        return None
    return re.sub ('\s+', ' ', string).strip()

def personname (node):
    """
    Get the name of a person from a DocBook node.
    """
    name = [None, None, None, None, None]
    namestr = None
    email = None
    for child in blip.data.xmliter (node):
        if child.type != 'element':
            continue
        if child.name == 'personname':
            namestr = personname(child)[0]
        elif child.name == 'email':
            email = child.getContent()
        elif namestr == None:
            try:
                i = ['honorific', 'firstname', 'othername', 'surname', 'lineage'].index(child.name)
                if name[i] == None:
                    name[i] = child.getContent()
            except ValueError:
                pass
    if namestr == None:
        while None in name:
            name.remove(None)
        namestr = ' '.join (name)
    return (normalize (namestr), normalize (email))
