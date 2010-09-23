# coding=UTF-8
# Copyright (c) 2010  Shaun McCance  <shaunm@gnome.org>
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

import blinq.reqs.web
import blinq.utils

import blip.db
import blip.html
import blip.utils

class DevelopersTab (blip.html.TabProvider):
    @classmethod
    def add_tabs (cls, page, request):
        if len(request.path) < 1 or request.path[0] != 'doc':
            return None
        if not (isinstance (request.record, blip.db.Branch) and request.record.type == u'Document'):
            return None
        cnt = blip.db.OutputFile.select (type=u'graphs', ident=request.record.ident, filename=u'topiclinks.svg')
        cnt = cnt.count()
        if cnt > 0:
            page.add_tab ('graph',
                          blip.utils.gettext ('Graph'),
                          blip.html.TabProvider.EXTRA_TAB)

    @classmethod
    def respond (cls, request):
        if len(request.path) < 1 or request.path[0] != 'doc':
            return None
        if not (isinstance (request.record, blip.db.Branch) and request.record.type == u'Document'):
            return None
        if not blip.html.TabProvider.match_tab (request, 'graph'):
            return None

        of = blip.db.OutputFile.select_one (type=u'graphs', ident=request.record.ident, filename=u'topiclinks.svg')
        
        response = blip.web.WebResponse (request)

        payload = blinq.reqs.web.TextPayload ()

        # It sure would be nice to serve inline SVG. Here's how:
        #payload.set_content (file(of.get_file_path()).read())
        #payload.content_type = 'image/svg+xml'

        # WebKit won't scale an SVG with object or embed.
        #payload.set_content ('<object data="%s" type="image/svg+xml" width="540px">' % of.get_blip_url())
        #payload.content_type = 'text/html'

        # Gecko won't show an SVG with img.
        #payload.set_content ('<img src="%s" width="540">' % of.get_blip_url())
        #payload.content_type = 'text/html'

        # So hey, let's use an iframe for now.
        payload.set_content ('<iframe src="%s" width="560" height="640">' % of.get_blip_url())
        payload.content_type = 'text/html'

        response.payload = payload
        return response
