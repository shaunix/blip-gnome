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

import ConfigParser
import re
import subprocess
import cStringIO
import os

import blip.db
import blip.utils

import blip.parsers
import blip.parsers.autoconf

import blip.plugins.modules.sweep

class KeyFileScanner (blip.plugins.modules.sweep.ModuleFileScanner):
    """
    ModuleFileScanner plugin for XDG applications.
    """

    def __init__ (self, scanner):
        blip.plugins.modules.sweep.ModuleFileScanner.__init__ (self, scanner)
        self._autoconf = None
        self._desktop_files = []
        self._commons = {}

    def process_file (self, dirname, basename):
        if dirname == self.scanner.repository.directory:
            if basename in ('configure.ac', 'configure.in'):
                self._autoconf = blip.parsers.get_parsed_file (blip.parsers.autoconf.Autoconf,
                                                               self.scanner.branch,
                                                               os.path.join (dirname, basename))
                return
        if basename == 'common.desktop.in':
            self._commons[dirname] = basename
        if re.match ('.*\.desktop(\.in)+$', basename):
            self._desktop_files.append ((dirname, basename))

    def post_process (self):
        for dirname, basename in self._desktop_files:
            self.post_process_file (dirname, basename)

    def post_process_file (self, dirname, basename):
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
                data['scm_dir'], data['scm_file'] = os.path.split (rel_ch)
                app = blip.db.Branch.select_one (type=u'Application', **data)
                if app is not None:
                    self.scanner.add_child (app)
                raise

            stamp.log ()

            contents = file(filename).read()

            # Some packages put common stuff in a common.desktop file and
            # merge them in at build time. Handle that.
            common = self._commons.get (dirname, None)
            if common is not None:
                common = file(os.path.join(dirname, common)).read()
                if not common.startswith('[Desktop Entry]\n'):
                    common = '[Desktop Entry]\n' + common
                contents = common + contents

            if basename.endswith ('.desktop.in.in'):
                if self._autoconf is not None:
                    atre = re.compile ('^([^@]*)(@[^@]+@)(.*)$')
                    def subsvar (line):
                        match = atre.match (line)
                        if match is None:
                            return line
                        ret = match.group(1)
                        ret += self._autoconf.get_variable (match.group(2)[1:-1], match.group(2))
                        ret += subsvar (match.group(3))
                        return ret
                    lines = [subsvar(line) for line in contents.split('\n')]
                    contents = '\n'.join (lines)
                base = os.path.basename (filename)[:-14]
            else:
                base = os.path.basename (filename)[:-11]

            owd = os.getcwd ()
            try:
                try:
                    os.chdir (self.scanner.repository.directory)
                    popen = subprocess.Popen (
                        'LC_ALL=C intltool-merge -d -q -u po - -',
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        shell=True)
                    contents, rv = popen.communicate (contents)
                    keyfile = KeyFile (cStringIO.StringIO (contents))
                finally:
                    os.chdir (owd)
            except Exception, e:
                return

            if not keyfile.has_group ('Desktop Entry'):
                return
            if not keyfile.has_key ('Desktop Entry', 'Type'):
                return
            if keyfile.get_value ('Desktop Entry', 'Type') != 'Application':
                return

            ident = u'/'.join(['/app', bserver, bmodule, base, bbranch])
            self.app = blip.db.Branch.get_or_create (ident, u'Application')

            name = keyfile.get_value ('Desktop Entry', 'Name')
            if name is not None:
                if isinstance (name, dict):
                    if name.has_key ('C'):
                        name = name['C']
                    else:
                        name = None
                if name is not None:
                    self.app.name = name

            if keyfile.has_key ('Desktop Entry', 'Comment'):
                desc = keyfile.get_value ('Desktop Entry', 'Comment')
                if isinstance (desc, dict):
                    if desc.has_key ('C'):
                        desc = desc['C']
                    else:
                        desc = None
                if desc is not None:
                    self.app.desc = desc

            for key in ('scm_type', 'scm_server', 'scm_module', 'scm_branch', 'scm_path'):
                setattr (self.app, key, getattr (branch, key))
            self.app.scm_dir, self.app.scm_file = os.path.split (rel_ch)

            self.scanner.add_child (self.app)

            self._try_icon_name = None
            if keyfile.has_key ('Desktop Entry', 'Icon'):
                iconname = keyfile.get_value ('Desktop Entry', 'Icon')
                if iconname == '@PACKAGE_NAME@':
                    iconname = branch.data.get ('pkgname', '@PACKAGE_NAME@')
                self._try_icon_name = iconname

    def FIXMEpost_process (self, **kw):
        images = self.scanner.get_plugin (pulse.plugins.images.ImagesHandler)
        for self.app, iconname in self.appicons:
            images.locate_icon (app, iconname)
        for app, docident in self.appdocs:
            doc = db.Branch.get (docident)
            if doc is not None:
                rel = db.Documentation.set_related (app, doc)
                if doc.data.has_key ('screenshot'):
                    app.data['screenshot'] = doc.data['screenshot']
                app.set_relations (db.Documentation, [rel])

class KeyFile (object):
    """
    Parse a KeyFile, like those defined by the Desktop Entry Specification.
    """

    def __init__ (self, fd):
        if isinstance (fd, basestring):
            fd = codecs.open (fd, 'r', 'utf-8')
        cfg = ConfigParser.ConfigParser()
        cfg.optionxform = str
        cfg.readfp (fd)
        self._data = {}
        for group in cfg.sections ():
            self._data[group] = {}
            for key, value in cfg.items (group):
                left = key.find ('[')
                right = key.find (']')
                if not isinstance (value, unicode):
                    value = unicode(value, 'utf-8')
                if left >= 0 and right > left:
                    keybase = key[0:left]
                    keylang = key[left+1:right]
                    self._data[group].setdefault (keybase, {})
                    if isinstance (self._data[group][keybase], basestring):
                        self._data[group][keybase] = {'C' : self._data[group][keybase]}
                    self._data[group][keybase][keylang] = value
                else:
                    if self._data[group].has_key (key):
                        if isinstance (self._data[group][key], dict):
                            self._data[group][key]['C'] = value
                        else:
                            raise pulse.utils.PulseException ('Duplicate entry for %s in %s'
                                                              % (key, fd.name))
                    else:
                        self._data[group][key] = value

    def get_groups (self):
        """Get the groups from the key file."""
        return self._data.keys()

    def has_group (self, group):
        """Check if the key file has a group."""
        return self._data.has_key (group)

    def get_keys (self, group):
        """Get the keys that are set in a group in the key file."""
        return self._data[group].keys()

    def has_key (self, group, key):
        """Check if a key is set in a group in the key file."""
        return self._data[group].has_key (key)

    def get_value (self, group, key):
        """Get the value of a key in a group in the key file."""
        return self._data[group][key]


