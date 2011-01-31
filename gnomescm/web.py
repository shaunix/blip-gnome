# coding=UTF-8
# Copyright (c) 2011  Shaun McCance  <shaunm@gnome.org>
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

import blip.plugins.commits.web

class GnomeCgitCommitLinkProvider (blip.plugins.commits.web.CgitCommitLinkProvider):
    @classmethod
    def get_cgit_root (cls, branch):
        if branch.scm_server == u'git://git.gnome.org/':
            return 'http://git.gnome.org/browse/'
        xdgpre = u'git://anongit.freedesktop.org/'
        if branch.scm_server.startswith (xdgpre):
            return 'http://cgit.freedesktop.org/' + branch.scm_server[len(xdgpre):]
        return None
