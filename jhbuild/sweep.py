# Copyright (c) 2006, 2010  Shaun McCance  <shaunm@gnome.org>
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

import os
import sys

import xml.dom.minidom

import blip.data
import blip.db
import blip.scm
import blip.sweep

import blip.plugins.sets.sweep

class JHBuildSetSweeper (blip.plugins.sets.sweep.SetSweeper):
    modulesets = {}

    @classmethod
    def sweep_set (cls, record, data, request):
        if not (data.has_key ('jhbuild_scm_type')   and
                data.has_key ('jhbuild_scm_server') and
                data.has_key ('jhbuild_scm_module') and
                data.has_key ('jhbuild_scm_dir')    and
                data.has_key ('jhbuild_scm_file')):
            return False
        repo = blip.scm.Repository (scm_type=data['jhbuild_scm_type'],
                                    scm_server=data['jhbuild_scm_server'],
                                    scm_module=data['jhbuild_scm_module'],
                                    scm_branch=data.get('jhbuild_scm_branch'),
                                    scm_path=data.get('jhbuild_scm_path'),
                                    update=request.get_tool_option ('update_scm', True))
        filename = os.path.join (repo.directory,
                                 data['jhbuild_scm_dir'],
                                 data['jhbuild_scm_file'])

        if not cls.modulesets.has_key (filename):
            cls.modulesets[filename] = ModuleSet (filename)
        moduleset = cls.modulesets[filename]

        packages = []
        if not data.has_key ('jhbuild_metamodule'):
            packages = moduleset.get_metamodule (os.path.basename (filename))
        else:
            modules = data['jhbuild_metamodule']
            if isinstance (modules, basestring):
                modules = [modules]

            for module in modules:
                if not moduleset.has_metamodule (module):
                    continue
                packages += moduleset.get_metamodule (module)

        newpkgs = []
        while len(packages) > 0:
            pkg = packages.pop ()
            if moduleset.has_package (pkg):
                newpkgs.append (pkg)
            elif moduleset.has_metamodule (pkg):
                for npkg in moduleset.get_metamodule (pkg):
                    packages.append (npkg)
        packages = newpkgs

        rels = []
        for pkg in packages:
            # FIXME
            branch = cls.update_branch (moduleset, pkg, request)
            if branch is not None:
                rels.append (blip.db.SetModule.set_related (record, branch))
        record.set_relations (blip.db.SetModule, rels)

    @classmethod
    def update_branch (cls, moduleset, key, request):
        if not moduleset.has_package (key):
            return None
        pkg_data = moduleset.get_package (key)

        data = {}
        for k in pkg_data.keys():
            if k[:4] == 'scm_':
                data[k] = pkg_data[k]

        try:
            repo = blip.scm.Repository (checkout=False, update=False, **pkg_data)
        except blip.scm.RepositoryError, err:
            blip.utils.warn (err.message)
            return
        servername = repo.server_name
        if servername == None:
            return None
        if not 'scm_branch' in pkg_data:
            pkg_data['scm_branch'] = repo.scm_branch
        ident = u'/'.join (['/mod', servername, pkg_data['scm_module'], pkg_data['scm_branch']])
        record = blip.db.Branch.get_or_create (ident, u'Module')
        record.update (data)
        if pkg_data.has_key ('autogenargs'):
            record.data['configure_args'] = pkg_data['autogenargs']
        pkg_data['__record__'] = record

        # FIXME: do we want to do deps from jhbuild?
        #records.setdefault (record.ident, {'record' : record, 'pkgdatas' : []})
        # Records could have package information defined in multiple modulesets,
        # so we record all of them.  See the comment in update_deps.
        #records[record.ident]['pkgdatas'].append ((moduleset, key))

        return record

class ModuleSet:
    def __init__ (self, filename):
        self._packages = {}
        self._metas = {}
        self.filename = filename
        self.parse (filename)

    def get_packages (self):
        return self._packages.keys()

    def has_package (self, key):
        return self._packages.has_key (key)

    def get_package (self, key):
        return self._packages[key]

    def get_metamodules (self):
        return self._metas.keys()

    def has_metamodule (self, key):
        return self._metas.has_key (key)

    def get_metamodule (self, key):
        return self._metas[key]

    def parse (self, filename):
        base = os.path.basename (filename)
        dom = xml.dom.minidom.parse (filename)
        repos = {}
        default_repo = None
        for node in dom.documentElement.childNodes:
            if node.nodeType != node.ELEMENT_NODE:
                continue
            if node.tagName == 'repository':
                repo_data = {}
                repo_data['scm_type'] = node.getAttribute ('type')
                if repo_data['scm_type'] == 'cvs':
                    repo_data['scm_server'] = node.getAttribute ('cvsroot')
                else:
                    repo_data['scm_server'] = node.getAttribute ('href')
                repo_data['repo_name'] = node.getAttribute ('name')
                if node.hasAttribute ('name'):
                    repos[node.getAttribute ('name')] = repo_data
                if node.getAttribute ('default') == 'yes':
                    default_repo = repo_data
            elif node.tagName == 'autotools':
                pkg_data = {'id' : node.getAttribute ('id')}
                if node.hasAttribute ('autogenargs'):
                    pkg_data['autogenargs'] = node.getAttribute ('autogenargs')
                for child in node.childNodes:
                    if child.nodeType != child.ELEMENT_NODE:
                        continue
                    if child.tagName == 'branch':
                        if child.hasAttribute ('repo'):
                            repo_data = repos.get (child.getAttribute ('repo'), None)
                        else:
                            repo_data = default_repo
                        if repo_data != None:
                            pkg_data['scm_type'] = repo_data['scm_type']
                            pkg_data['scm_server'] = repo_data['scm_server']
                        pkg_data['scm_module'] = pkg_data['id']
                        if child.hasAttribute ('module'):
                            pkg_data['scm_path'] = child.getAttribute ('module')
                        if child.hasAttribute ('revision'):
                            pkg_data['scm_branch'] = child.getAttribute ('revision')
                        else:
                            pkg_data['scm_branch'] = blip.scm.Repository.get_default_branch(pkg_data['scm_type'])
                    elif child.tagName == 'dependencies':
                        deps = []
                        for dep in child.childNodes:
                            if dep.nodeType == dep.ELEMENT_NODE and dep.tagName == 'dep':
                                deps.append (dep.getAttribute ('package'))
                        pkg_data['deps'] = deps
                if pkg_data.has_key ('scm_type'):
                    self._packages[pkg_data['id']] = pkg_data
                    self._metas.setdefault (base, [])
                    self._metas[base].append (pkg_data['id'])
            elif node.tagName == 'metamodule':
                meta = []
                for deps in node.childNodes:
                    if deps.nodeType == deps.ELEMENT_NODE and deps.tagName == 'dependencies':
                        for dep in deps.childNodes:
                            if dep.nodeType == dep.ELEMENT_NODE and dep.tagName == 'dep':
                                meta.append (dep.getAttribute ('package'))
                        break
                self._metas[node.getAttribute ('id')] = meta
            elif node.tagName == 'include':
                href = node.getAttribute ('href')
                if not href.startswith ('http:'):
                    self.parse (os.path.join (os.path.dirname (filename), href))


# FIXME
def __update_deps ():
    for ident, recdata in records.items():
        # Records could have package information defined in multiple modulesets.
        # If the jhbuild maintainers are on the ball, the dependencies in either
        # should be equivalent, except they might end up pointing to different
        # branches, as a result of what branches of other modules are included
        # in the particular moduleset.
        #
        # I toyed around with having dependencies go from Branch to Branchable,
        # which would make this a moot point, but it makes it difficult to do
        # dependency graphs, because you have to arbitrarily choose branches
        # of dependencies, and that could affect further dependencies.
        #
        # So we arbitrarity take the first moduleset.  It's probably a good
        # idea to keep newer modulesets first in sets.xml.
        moduleset, pkgkey = recdata['pkgdatas'][0]
        rec = recdata['record']
        pkgdata = moduleset.get_package (pkgkey)
        deps = get_deps (moduleset, pkgkey)
        pkgrels = []
        pkgdrels = []
        for dep in deps:
            depdata = moduleset.get_package (dep)
            if not depdata.has_key ('__record__'): continue
            deprec = depdata['__record__']
            rel = pulse.db.ModuleDependency.set_related (rec, deprec)
            pkgrels.append (rel)
            direct = (dep in pkgdata['deps'])
            if rel.direct != direct:
                rel.direct = direct
        rec.set_relations (pulse.db.ModuleDependency, pkgrels)

# FIXME
#known_deps = {}
def ___get_deps (moduleset, pkg, seen=[]):
    depskey = moduleset.filename + ':' + pkg
    if known_deps.has_key (depskey):
        return known_deps[depskey]
    pkgdata = moduleset.get_package (pkg)
    deps = []
    for dep in pkgdata.get('deps', []):
        # Prevent infinite loops for circular dependencies
        if dep in seen: continue
        if not moduleset.has_package (dep): continue
        depdata = moduleset.get_package (dep)
        if not dep in deps:
            deps.append (dep)
            for depdep in get_deps (moduleset, dep, seen + [pkg]):
                if not depdep in deps:
                    deps.append (depdep)
    known_deps[depskey] = deps
    return deps


