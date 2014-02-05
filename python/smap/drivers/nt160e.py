"""
Copyright (c) 2011, 2012, Regents of the University of California
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

 - Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
 - Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the
   distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL
THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
OF THE POSSIBILITY OF SUCH DAMAGE.
"""
"""
@author Jay Taneja <taneja@cs.berkeley.edu>
@author Tyler Hoyt <thoyt@berkeley.edu>
"""

import time
import calendar
import re
import urllib2, base64
import logging,os,sys

from twisted.internet import defer
from twisted.web import client
from twisted.python import log

from selenium import webdriver

from smap import util, actuate
from smap.driver import SmapDriver

class NT160e(SmapDriver):
    """Periodically scrape data from Proliphix NT160e thermostat"""

    # 'Name of field': ("Units",[1st or 2nd group],[zero-indexed column])
    FIELDS = {
        'zone_temp': ('C','avgtemp ='),
        'humidity': ('rh','printFSC\("Relative Humidity"'),
        'cool_setting': ('C','printFSC\("Cool Setting"'),
        'heat_setting': ('F','printFSC\("Heat Setting"'),
        'schedule_cool': ('C','printFSC\("Cool"'),
        'schedule_heat': ('C','printFSC\("Heat"'),
        'hvac_state': ('state','hvacStateS =')
    }
    ACTUATORS = [
        {'name': 'fan_mode', 'OID': 'OID4.1.3', 'unit': 'mode', 'type': 'long', 'actuator_type': 'Discrete'},
        {'name': 'hvac_mode', 'OID': 'OID4.1.1' , 'unit': 'mode', 'type': 'long', 'actuator_type': 'Discrete'},
        {'name': 'hold', 'OID': 'holdmode', 'unit': 'mode', 'type': 'long', 'actuator_type': 'Discrete'},
        {'name': 'cool_setting', 'OID': 'OID4.1.6', 'unit': 'F', 'type': 'double', 'actuator_type': 'Continuous'},
        {'name': 'heat_setting', 'OID': 'OID4.1.5', 'unit': 'F', 'type': 'double', 'actuator_type': 'Continuous'}
    ]
       
    def convertTemperature(self, temp, scale):
        if scale.upper() == 'FAHRENHEIT':
            return (float(temp) - 32) / 1.8
        else:
            return float(temp)

    def scrape(self):
        current_values = {}
        try:
            request = urllib2.Request(self.baseurl + 'index.shtml')
            base64string = base64.encodestring('%s:%s' % (self.auth[0], self.auth[1])).replace('\n', '')
            request.add_header("Authorization", "Basic %s" % base64string)
            response = urllib2.urlopen(request)
            page1 = response.read()

            request = urllib2.Request(self.baseurl + 'settings.shtml')
            request.add_header("Authorization", "Basic %s" % base64string)
            response = urllib2.urlopen(request)

            page2 = response.read()

            scale = ''

            for line in page2.split('\n'):
                if re.match('printFSC\("Temperature Scale"', line):
                    scale = line.split('"')[3]
                    break

            for field in self.FIELDS.keys():
                items = re.search(self.FIELDS[field][1] + '.+', page1)
                if items:
                    if field == 'hvac_state':
                        state = items.group(0).split('"')[1]
                        if state.upper() == 'OFF':
                            value = 0
                        elif state.upper() == 'COOL':
                            value = 1
                        elif state.upper() == 'COOL2':
                            value = 2
                        elif state.upper() == 'HEAT':
                            value = 3
                        elif state.upper() == 'HEAT2':
                            value = 4
                        elif state.upper() == 'AUX HT':
                            value = 5
                        elif state.upper() == 'DELAY':
                            value = 6
                        else:
                            value = 7
                    elif field == 'zone_temp':
                        value = self.convertTemperature(items.group(0).split('"')[1],scale)
                    elif self.FIELDS[field][0] == 'C':
                        value = self.convertTemperature(items.group(0).split('"')[3],scale)
                    else:
                        value = items.group(0).split('"')[3]

                    if re.match('\D{1}',str(value)[-1]):
                        value = int(str(value)[:-1])
                    current_values[field] = float(value)
                    have_data = True
            reading_time = time.mktime(time.localtime())
        except Exception, e:
            print e
            fname = os.path.split(sys.exc_info()[2].tb_frame.f_code.co_filename)[1]
            log.err()
            logging.error('Error (' + str(sys.exc_info()[0]) + ') : ' + str(e) + ' from ' + fname + ':' + str(sys.exc_info()[2].tb_lineno))
            have_data = False

        if have_data == True:
            try:
                for field in self.FIELDS.keys():
                    self.add('/' + field, reading_time, current_values[field])
                have_data = False
            except Exception,e:
                print e
                fname = os.path.split(sys.exc_info()[2].tb_frame.f_code.co_filename)[1]
                log.err()
                logging.error('Error (' + str(sys.exc_info()[0]) + ') : ' + str(e) + ' from ' + fname + ':' + str(sys.exc_info()[2].tb_lineno))

    def setup(self, opts):
        self.baseurl = opts.get('url')
        self.auth = [opts.get('login'),opts.get('password')]

        for stream, meta in self.FIELDS.iteritems():
            log.msg('adding stream /' + opts.get('key') + '/' + stream)
            logging.info('adding stream /' + opts.get('key') + '/' + stream)
            self.add_timeseries('/' + stream, meta[0],
                                data_type='double',
                                timezone='America/Los_Angeles')

            self.set_metadata('/' + stream, {
                'Metadata/Extra/Measurement' : stream,
                })

        self.set_metadata('/', {
                'Location/Uri' : self.baseurl,
                'Metadata/Location/Area' : 'California',
                'Metadata/Location/Country' : 'USA',
                })

        # Actuators
        self.auth_url = 'https://' + self.auth[0] + ':' + self.auth[1] + '@' + self.baseurl
        s = {'url': self.auth_url, 'uname': self.auth[0], 'password': self.auth[1]}
        for a in self.ACTUATORS:
            s['OID'] = a['OID']
            if a['actuator_type'] == 'Discrete':
                klass = DiscreteActuator
            elif a['actuator_type'] == 'Continuous':
                klass = ContinuousActuator
            print 'adding actuator', klass, s
            self.add_actuator('/' + a['name'], a['unit'], klass, setup=s, data_type=a['type'], write_limit=5)
            print 'added'
        
    def start(self):
        self.scraper = util.periodicSequentialCall(self.scrape)
        self.scraper.start(60)

    def stop(self):
        self.scraper.stop()

if __name__=='__main__':
    url = "http://admin:admin@192.168.1.82/index.shtml"
    cmd = "document.getElementsByName('OID4.1.6')[0].value = 820;"
    browser = webdriver.Firefox()
    browser.get(url)
    browser.execute_script(cmd)
    browser.execute_script("document.getElementsByName('submit')[0].click();")
    browser.quit()

class _NT160eActuator(actuate.SmapActuator):

    def setup(self, opts):
        print 'NT160eActuator setup'
        self.url = opts.get('auth_url')
        self.uname = opts.get('uname')
        self.password = opts.get('password')

    def get_state(self, request):
        pass

    def set_state(self, request, state):
        cmd = "document.getElementsByName('OID4.1.6')[0].value = 820;"
        browser = webdriver.Firefox()
        browser.get(self.auth_url)
        browser.execute_script(cmd)
        browser.execute_script("document.getElementsByName('submit')[0].click();")
        browser.quit()

class DiscreteActuator(_NT160eActuator, actuate.NStateActuator):
    def setup(self, opts):
        print 'DiscreteActuator setup'
        actuate.NStateActuator.setup(self, opts)
        _NT160eActuator.setup(self, opts)

class ContinuousActuator(_NT160eActuator, actuate.ContinuousActuator):
    def setup(self, opts):
        print 'ContinuousActuator setup'
        actuate.ContinuousActuator.setup(self, opts)
        _NT160eActuator.setup(self, opts) 
