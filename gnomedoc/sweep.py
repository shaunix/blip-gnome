# Copyright (c) 2006-2009  Shaun McCance  <shaunm@gnome.org>
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

MALLARD_NS = 'http://projectmallard.org/1.0/'

_STATUSES = {'none':       '00',
             'stub':       '10',
             'incomplete': '20',
             'draft':      '30',
             'outdated':   '40',
             'review':     '50',
             'candidate':  '60',
             'final':      '70'}
def get_status (status):
    if _STATUSES.has_key (status):
        return _STATUSES[status] + status
    else:
        return '00none'


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
                                                     self.scanner.branch, filename)

            is_gdu_doc = False
            for line in makefile.get_lines ():
                if line.startswith ('include $(top_srcdir)/'):
                    if line.endswith ('gnome-doc-utils.make'):
                        is_gdu_doc = True
                        break
            if not is_gdu_doc:
                return

            stamp.log ()

            if 'DOC_MODULE' in makefile:
                doc_id = makefile['DOC_MODULE']
                doc_type = u'docbook'
            elif 'DOC_ID' in makefile:
                doc_id = makefile['DOC_ID']
                doc_type = u'mallard'
            else:
                return
            if doc_id == '@PACKAGE_NAME@':
                doc_id = branch.data.get ('pkgname')
            if doc_id is None:
                return
            ident = u'/'.join(['/doc', bserver, bmodule, doc_id, bbranch])
            document = blip.db.Branch.get_or_create (ident, u'Document')
            document.parent = branch

            for key in ('scm_type', 'scm_server', 'scm_module', 'scm_branch', 'scm_path'):
                setattr (document, key, getattr (branch, key))
            document.subtype = u'gdu-' + doc_type
            document.scm_dir = blip.utils.relative_path (os.path.join (dirname, 'C'),
                                                         self.scanner.repository.directory)
            if doc_type == 'docbook':
                document.scm_file = blip.utils.utf8dec (doc_id) + u'.xml'
            else:
                # FIXME: plugin sets won't have this
                document.scm_file = u'index.page'

            fnames = (makefile.get('DOC_PAGES', '').split() +
                      makefile.get('DOC_INCLUDES', '').split())
            if doc_type == u'docbook':
                fnames.append (doc_id + '.xml')
            document.data['xml2po_files'] = [os.path.join ('C', fname) for fname in fnames]
            fnames += makefile.get('DOC_ENTITIES', '').split()
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

            self.scanner.add_child (document)
            self.documents.append (document)
            for translation in translations:
                self.translations.append (translation)

    def post_process (self):
        for document in self.documents: 
            with blip.db.Error.catch (document):
                if document.subtype == u'gdu-docbook':
                    GnomeDocScanner.process_docbook (document, self.scanner)
                elif document.subtype == u'gdu-mallard':
                    GnomeDocScanner.process_mallard (document, self.scanner)
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
            rev = blip.db.Revision.get_last_revision (branch=document.parent,
                                                      files=[os.path.join (translation.scm_dir,
                                                                           translation.scm_file)])
            if rev is not None:
                translation.mod_datetime = rev.datetime
                translation.mod_person = rev.person
            translation.updated = datetime.datetime.utcnow()

    @classmethod
    def process_docbook (cls, document, scanner):
        filename = os.path.join (scanner.repository.directory,
                                 document.scm_dir, document.scm_file)
        rel_scm = blip.utils.relative_path (filename, blinq.config.scm_dir)
        blip.utils.log ('Processing %s' % rel_scm)

        title = None
        abstract = None
        credits = []
        process = False
        with blip.db.Error.catch (document):
            ctxt = libxml2.newParserCtxt ()
            xmldoc = ctxt.ctxtReadFile (filename, None,
                                        libxml2.XML_PARSE_DTDLOAD | libxml2.XML_PARSE_NOCDATA |
                                        libxml2.XML_PARSE_NOENT | libxml2.XML_PARSE_NONET)
            xmldoc.xincludeProcess ()
            root = xmldoc.getRootElement ()
            process = True
        if not process:
            return
        seen = 0
        document.data['status'] = '00none'
        for node in xmliter (root):
            if node.type != 'element':
                continue
            if node.name[-4:] == 'info':
                seen += 1
                infonodes = list (xmliter (node))
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
                        if infonode.prop ('revision') == document.parent.data.get ('pkgseries'):
                            document.data['docstatus'] = get_status (infonode.prop ('role'))
                    elif infonode.name == 'authorgroup':
                        infonodes.extend (list (xmliter (infonode)))
                    elif infonode.name in ('author', 'editor', 'othercredit'):
                        cr_name, cr_email = personname (infonode)
                        maint = (infonode.prop ('role') == 'maintainer')
                        credits.append ((cr_name, cr_email, infonode.name, maint))
                    elif infonode.name == 'collab':
                        cr_name = None
                        for ch in xmliter (infonode):
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
                        for ch in xmliter (infonode):
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
                rel.author = (cr_type in ('author', 'corpauthor'))
                rel.editor = (cr_type == 'editor')
                rel.publisher = (cr_type == 'publisher')
                rel.maintainer = (cr_maint == True)
                rels.append (rel)
        document.set_relations (blip.db.DocumentEntity, rels)

    @classmethod
    def process_mallard (cls, document, scanner):
        cache = blip.db.CacheData.get ((document.ident, u'mallard-pages'))
        if cache is None:
            cache = blip.db.CacheData (ident=document.ident,
                                       key=u'mallard-pages')

        for basename in document.data.get ('scm_files', []):
            filename = os.path.join (scanner.repository.directory,
                                     document.scm_dir, basename)
            with blip.db.Error.catch (document, ctxt=basename):
                with blip.db.Timestamp.stamped (filename, scanner.repository) as stamp:
                    stamp.check (scanner.request.get_tool_option ('timestamps'))
                    stamp.log ()
                    cls.process_mallard_page (document, filename, cache, scanner)

        doccredits = {}
        topiclinks = {}
        for pageid in cache.data.keys():
            if pageid.startswith('/'):
                continue
            for cr_name, cr_email, cr_types in cache.data[pageid].get('credits', []):
                doccredits.setdefault (cr_email, {})
                doccredits[cr_email].setdefault ('name', cr_name)
                for badge in ('maintainer', 'author','editor', 'publisher'):
                    doccredits[cr_email].setdefault (badge, False)
                    if badge in cr_types:
                        doccredits[cr_email][badge] = True
            topiclinks.setdefault (pageid, [])
            for xref in cache.data[pageid]['topiclinks']:
                if xref not in topiclinks[pageid]:
                    topiclinks[pageid].append (xref)
            for xref in cache.data[pageid]['guidelinks']:
                topiclinks.setdefault (xref, [])
                if pageid not in topiclinks[xref]:
                    topiclinks[xref].append (pageid)
        rels = []
        for cr_email in doccredits.keys():
            ent = None
            if cr_email is not None:
                ent = blip.db.Entity.get_or_create_email (cr_email)
            if ent is None:
                ident = u'/ghost/' + urllib.quote (cr_name)
                ent = blip.db.Entity.get_or_create (ident, u'Ghost')
            if ent is not None:
                rel = blip.db.DocumentEntity.set_related (document, ent)
                for badge in ('maintainer', 'author', 'editor', 'pulisher'):
                    setattr (rel, badge, badge in cr_types)
                rels.append (rel)
        document.set_relations (blip.db.DocumentEntity, rels)
        dot = ''
        for pageid in sorted (topiclinks.keys()):
            for xref in sorted (topiclinks[pageid]):
                dot += ('"%s" -> "%s";\n' % (pageid, xref))
        if dot != cache.data.get('/dot', ''):
            of = blip.db.OutputFile.select_one (type=u'graphs', ident=document.ident, filename=u'topiclinks.svg')
            if of is None:
                of = blip.db.OutputFile (type=u'graphs', ident=document.ident,
                                         filename=u'topiclinks.svg',
                                         datetime=datetime.datetime.now())
            of.makedirs ()
            outfile = of.get_file_path ()
            outfile_rel = blip.utils.relative_path (outfile,
                                                    os.path.join (blinq.config.web_files_dir, 'graphs'))
            blip.utils.log ('Creating link graph %s' % outfile_rel)
            fulldot = ('strict digraph topics {\n' +
                       'width="3";\n' +
                       'rankdir="LR";\n' +
                       'splines="ortho";\n' +
                       'node [shape=box,width=2,fontname=sans,fontsize=10];\n' +
                       dot + '}\n')
            popen = subprocess.Popen (['dot', '-Tsvg', '-o', outfile], stdin=subprocess.PIPE)
            popen.communicate (fulldot)


    @classmethod
    def process_mallard_page (cls, document, filename, cache, scanner):
        title = None
        desc = None
        ctxt = libxml2.newParserCtxt ()
        xmldoc = ctxt.ctxtReadFile (filename, None,
                                    libxml2.XML_PARSE_DTDLOAD | libxml2.XML_PARSE_NOCDATA |
                                    libxml2.XML_PARSE_NOENT | libxml2.XML_PARSE_NONET)
        xmldoc.xincludeProcess ()
        root = xmldoc.getRootElement ()
        if not _is_ns_name (root, MALLARD_NS, 'page'):
            return
        pageid = root.prop ('id')
        cache.data.setdefault (pageid, {})
        cache.data[pageid]['topiclinks'] = []
        cache.data[pageid]['guidelinks'] = []
        pkgseries = document.parent.data.get ('pkgseries', None)
        revision = {}
        def process_link_node (linknode):
            linktype = linknode.prop ('type')
            if linktype not in ('topic', 'guide'):
                return
            xref = linknode.prop ('xref')
            xrefhash = xref.find ('#')
            if xrefhash >= 0:
                xref = xref[:xrefhash]
            if xref != '':
                lst = cache.data[pageid][linktype + 'links']
                if xref not in lst:
                    lst.append (xref)
        for node in xmliter (root):
            if node.type != 'element':
                continue
            if _is_ns_name (node, MALLARD_NS, 'info'):
                for infonode in xmliter (node):
                    if infonode.type != 'element':
                        continue
                    if _is_ns_name (infonode, MALLARD_NS, 'title'):
                        if infonode.prop ('type') == 'text':
                            title = normalize (infonode.getContent ())
                    elif _is_ns_name (infonode, MALLARD_NS, 'desc'):
                        desc = normalize (infonode.getContent ())
                    elif _is_ns_name (infonode, MALLARD_NS, 'revision'):
                        if pkgseries is not None:
                            for prop in ('version', 'docversion', 'pkgversion'):
                                if infonode.prop (prop) == pkgseries:
                                    revdate = infonode.prop ('date')
                                    revstatus = infonode.prop ('status')
                                    if (not revision.has_key (prop)) or (revdate > revision[prop][0]):
                                        revision[prop] = (revdate, revstatus)
                    elif _is_ns_name (infonode, MALLARD_NS, 'credit'):
                        types = infonode.prop ('type')
                        if isinstance (types, basestring):
                            types = types.split ()
                        else:
                            types = []
                        crname = cremail = None
                        for crnode in xmliter (infonode):
                            if _is_ns_name (crnode, MALLARD_NS, 'name'):
                                crname = normalize (crnode.getContent ())
                            elif _is_ns_name (crnode, MALLARD_NS, 'email'):
                                cremail = normalize (crnode.getContent ())
                        if crname is not None or cremail is not None:
                            cache.data[pageid].setdefault ('credits', [])
                            cache.data[pageid]['credits'].append (
                                (crname, cremail, types))
                    elif _is_ns_name (infonode, MALLARD_NS, 'link'):
                        process_link_node (infonode)
            elif _is_ns_name (node, MALLARD_NS, 'title'):
                if title is None:
                    title = normalize (node.getContent ())
            elif _is_ns_name (node, MALLARD_NS, 'section'):
                for child in xmliter (node):
                    if child.type != 'element':
                        continue
                    if _is_ns_name (child, MALLARD_NS, 'info'):
                        for infonode in xmliter (child):
                            if infonode.type != 'element':
                                continue
                            if _is_ns_name (infonode, MALLARD_NS, 'link'):
                                process_link_node (infonode)

        docstatus = None
        docdate = None
        if pageid is not None:
            ident = u'/page/' + pageid + document.ident
            page = blip.db.Branch.get_or_create (ident, u'DocumentPage')
            page.parent = document
            for key in ('scm_type', 'scm_server', 'scm_module', 'scm_branch', 'scm_path', 'scm_dir'):
                setattr (page, key, getattr (document, key))
            page.scm_file = os.path.basename (filename)
            if title is not None:
                page.name = blip.utils.utf8dec (title)
            if desc is not None:
                page.desc = blip.utils.utf8dec (desc)
            for prop in ('pkgversion', 'docversion', 'version'):
                if revision.has_key (prop):
                    (docdate, docstatus) = revision[prop]
                    docstatus = get_status (docstatus)
                    page.data['docstatus'] = docstatus
                    page.data['docdate'] = docdate
            rels = []
            for cr_name, cr_email, cr_types in cache.data[pageid].get('credits', []):
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
                    rel = blip.db.DocumentEntity.set_related (page, ent)
                    for badge in ('maintainer', 'author', 'editor', 'pulisher'):
                        setattr (rel, badge, badge in cr_types)
                    rels.append (rel)
            page.set_relations (blip.db.DocumentEntity, rels)

        if pageid == 'index':
            if title is not None:
                document.name = blip.utils.utf8dec (title)
            if desc is not None:
                document.desc = blip.utils.utf8dec (desc)
            document.data['docstatus'] = docstatus
            document.data['docdate'] = docdate

    @classmethod
    def process_xml2po (cls, translation, scanner):
        return
        filename = os.path.join (scanner.repository.directory,
                                 translation.scm_dir, translation.scm_file)
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

            stamp.log ()

            makedir = os.path.join (scanner.repository.directory,
                                    os.path.dirname (translation.scm_dir))
            cmd = 'msgmerge "%s" "%s" 2>&1' % (
                os.path.join (os.path.basename (translation.scm_dir), translation.scm_file),
                potfile.get_file_path ())
            owd = os.getcwd ()
            try:
                os.chdir (makedir)
                pofile = blip.parsers.po.Po (scanner.branch, os.popen (cmd))
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

        doc_id = translation.ident.split('/')[-2]
        doc_files = translation.parent.data.get ('xml2po_files', [])
        potfile = doc_id + u'.pot'
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
                cls.potfiles[indir] = of
                return of

        of.makedirs ()
        cmd = 'xml2po -e -o "' + potfile_abs + '" "' + '" "'.join(doc_files) + '"'
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
    for child in xmliter (node):
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

def _get_ns (node):
    ns = node.ns()
    if ns is not None:
        return ns.getContent ()
    return None

def _is_ns_name (node, ns, name):
    return (_get_ns (node) == ns and node.name == name)

def xmliter (node):
    child = node.children
    while child:
        yield child
        child = child.next
