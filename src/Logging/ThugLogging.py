#!/usr/bin/env python
#
# ThugLogging.py
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA  02111-1307  USA

from .BaseLogging import BaseLogging
from .SampleLogging import SampleLogging
from .LoggingModules import LoggingModules
from Analysis.virustotal.VirusTotal import VirusTotal
from Analysis.honeyagent.HoneyAgent import HoneyAgent

try:
    import configparser as ConfigParser
except ImportError:
    import ConfigParser

import os
import errno
import copy

import logging
log = logging.getLogger("Thug")

class ThugLogging(BaseLogging, SampleLogging):
    eval_min_length_logging = 4

    def __init__(self, thug_version):
        BaseLogging.__init__(self)
        SampleLogging.__init__(self)

        self.thug_version   = thug_version
        self.VirusTotal     = VirusTotal()
        self.HoneyAgent     = HoneyAgent()
        self.baseDir        = None
        self.windows        = dict()
        self.shellcodes     = set()
        self.shellcode_urls = set()
        self.methods_cache  = dict()
        self.formats        = set()

        self.__init_config()

    def __init_config(self):
        self.modules = dict()
        config       = ConfigParser.ConfigParser()

        conf_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logging.conf')
        config.read(conf_file)

        for name, module in LoggingModules.items():
            if self.check_module(name, config):
                self.modules[name.strip()] = module(self.thug_version)

        for m in self.modules.values():
            for format in getattr(m, 'formats', tuple()):
                self.formats.add(format)

    def resolve_method(self, name):
        if name in self.methods_cache.keys():
            return self.methods_cache[name]

        methods = []

        for module in self.modules.values():
            m = getattr(module, name, None)
            if m:
                methods.append(m)

        self.methods_cache[name] = methods
        return methods

    def set_url(self, url):
        for m in self.resolve_method('set_url'):
            m(url)

    def add_behavior_warn(self, description = None, cve = None, method = "Dynamic Analysis"):
        for m in self.resolve_method('add_behavior_warn'):
            m(description, cve, method)

        log.warning(description)

    def check_snippet(self, s):
        return len(s) < self.eval_min_length_logging

    def add_code_snippet(self, snippet, language, relationship, method = "Dynamic Analysis", check = False):
        if check and self.check_snippet(snippet):
            return

        for m in self.resolve_method('add_code_snippet'):
            m(snippet, language, relationship, method)

    def log_file(self, data, url = None, params = None):
        sample = self.build_sample(data, url)
        if sample is None:
            return None

        return self.__log_file(sample, data, url, params)

    def __log_file(self, sample, data, url = None, params = None):
        for m in self.resolve_method('log_file'):
            m(copy.deepcopy(sample), url, params)

        self.VirusTotal.analyze(data, sample, self.baseDir)

        if sample['type'] in ('JAR', ):
            self.HoneyAgent.analyze(data, sample, self.baseDir, params)

        log.SampleClassifier.classify(data, sample['md5'])
        return sample

    def log_event(self):
        for m in self.resolve_method('export'):
            m(self.baseDir)

        for m in self.resolve_method('log_event'):
            m(self.baseDir)

        if log.ThugOpts.file_logging:
            log.warning("Thug analysis logs saved at %s" % (self.baseDir, ))

    def log_connection(self, source, destination, method, flags = {}):
        """
        Log the connection (redirection, link) between two pages

        @source         The origin page
        @destination    The page the user is made to load next
        @method         Link, iframe, .... that moves the user from source to destination
        @flags          Additional information flags. Existing are: "exploit"
        """
        for m in self.resolve_method('log_connection'):
            m(source, destination, method, flags)

    def log_location(self, url, data, flags = {}):
        """
        Log file information for a given url

        @url    URL we fetched this file from
        @data   File dictionary data
                    Keys:
                        - content     Content
                        - md5         MD5 checksum
                        - sha256      SHA-256 checksum
                        - fsize       Content size
                        - ctype       Content type (whatever the server says it is)
                        - mtype       Calculated MIME type

        @flags  Additional information flags
        """
        for m in self.resolve_method('log_location'):
            m(url, data, flags = flags)

    def log_exploit_event(self, url, module, description, cve = None, data = None, forward = True):
        """
        Log file information for a given url

        @url            URL where this exploit occured
        @module         Module/ActiveX Control, ... that gets exploited
        @description    Description of the exploit
        @cve            CVE number (if available)
        @forward        Forward log to add_behavior_warn
        """
        if forward:
            self.add_behavior_warn("[%s] %s" % (module, description, ), cve = cve)

        for m in self.resolve_method('log_exploit_event'):
            m(url, module, description, cve = cve, data = data)

    def log_warning(self, data):
        log.warning(data)

        for m in self.resolve_method('log_warning'):
            m(data)

    def log_redirect(self, response):
        if not response:
            return None

        if not response.history:
            if response.url:
                log.URLClassifier.classify(response.url)
                log.HTTPSession.fetch_ssl_certificate(response.url)

            return None

        final = response.url

        while final is None:
            for h in reversed(response.history):
                final = h.url

        for h in response.history:
            location = h.headers.get('location', None)

            self.add_behavior_warn("[HTTP Redirection (Status: %s)] Content-Location: %s --> Location: %s" % (h.status_code,
                                                                                                              h.url,
                                                                                                              location))
            self.log_connection(h.url, location, "http-redirect")

            log.URLClassifier.classify(h.url)
            log.HTTPSession.fetch_ssl_certificate(h.url)

        log.URLClassifier.classify(final)
        log.HTTPSession.fetch_ssl_certificate(final)

        return final

    def log_href_redirect(self, referer, url):
        self.add_behavior_warn("[HREF Redirection (document.location)] Content-Location: %s --> Location: %s" % (referer, url, ))
        self.log_connection(referer, url, "href")

    def log_certificate(self, url, certificate):
        self.add_behavior_warn("[Certificate]\n %s" % (certificate, ))

        for m in self.resolve_method('log_certificate'):
            m(url, certificate)

    def log_analysis_module(self, dirname, sample, report, module, format = "json"):
        filename = "%s.%s" % (sample['md5'], format, )
        self.store_content(dirname, filename, report)

        method = "log_%s" % (module, )
        for m in self.resolve_method(method):
            m(sample, report)

    def log_virustotal(self, dirname, sample, report):
        self.log_analysis_module(dirname, sample, report, "virustotal")

    def log_honeyagent(self, dirname, sample, report):
        self.log_analysis_module(dirname, sample, report, "honeyagent")

    def log_androguard(self, dirname, sample, report):
        self.__log_file(sample, sample.pop("raw", None))
        self.log_analysis_module(dirname, sample, report, "androguard", "txt")

    def log_peepdf(self, dirname, sample, report):
        self.log_analysis_module(dirname, sample, report, "peepdf", "xml")

    def store_content(self, dirname, filename, content):
        """
        This method is meant to be used when a content (downloaded
        pages, samples, reports, etc. ) has to be saved in a flat
        file.

        @dirname    The directory where to store content
        @filename   The file where to store content
        @content    The content to be stored
        """
        if not log.ThugOpts.file_logging:
            return

        try:
            os.makedirs(dirname)
        except OSError as e:
            if e.errno == errno.EEXIST:
                pass
            else:
                raise

        fname = os.path.join(dirname, filename)

        with open(fname, 'wb') as fd:
            fd.write(content)

        return fname
