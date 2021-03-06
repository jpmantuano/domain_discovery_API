from os.path import isfile, join, exists, isdir
from os import chdir, listdir, environ, makedirs, rename, chmod, walk, remove
import linecache
from sys import exc_info

from subprocess import Popen
from subprocess import PIPE

import urllib2
import base64
import json
from zipfile import ZipFile

from elastic.search_documents import multifield_term_search
from elastic.misc_queries import exec_query
from elastic.aggregations import get_unique_values
from elastic.get_config import get_available_domains, get_model_tags
from elastic.config import es
from elastic.add_documents import update_document

from ache_config import ache_focused_crawler_server, ache_focused_crawler_port, ache_deep_crawler_server, ache_deep_crawler_port

import requests
from requests.exceptions import ConnectionError

from pprint import pprint

class CrawlerModel():

    def __init__(self, path):
        self._path = path
        self._all = 100000
        self._es = es
        self._termsIndex = "ddt_terms"
        self.runningCrawlers={}
        self._domains = get_available_domains(self._es)
        self._mapping = {"url":"url", "timestamp":"retrieved", "text":"text", "html":"html", "tag":"tag", "query":"query", "domain":"domain", "title":"title"}
        self._servers = {"focused": "http://"+ache_focused_crawler_server+":"+ache_focused_crawler_port,
                         "deep": 'http://'+ache_deep_crawler_server+":"+ache_deep_crawler_port}

    def _encode(self, url):
        return urllib2.quote(url).replace("/", "%2F")


    def _esInfo(self, domainId):
        es_info = {
            "activeDomainIndex": self._domains[domainId]['index'],
            "docType": self._domains[domainId]['doc_type']
        }
        if not self._domains[domainId].get("mapping") is None:
            es_info["mapping"] = self._domains[domainId]["mapping"]
        else:
            es_info["mapping"] = self._mapping
        return es_info

    def getCrawlerServers(self):
        return self._servers
    
    def updateDomains(self):
        self._domains = get_available_domains(self._es)

    def crawlerStopped(self, type, session):
        domainId = session["domainId"]

        self.runningCrawlers[domainId].pop(type)
        if len(self.runningCrawlers[domainId].keys()) == 0:
            self.runningCrawlers.pop(domainId)

    #######################################################################################################
    # Generate Model
    #######################################################################################################

    def createModel(self, session=None, zip=True):
        """ Create an ACHE model to be applied to SeedFinder and focused crawler.
        It saves the classifiers, features, the training data in the <project>/data/<domain> directory.
        If zip=True all generated files and folders are zipped into a file.

        Parameters:
        session (json): should have domainId

        Returns:
        Zip file url or message text
        """
        path = self._path

        es_info = self._esInfo(session["domainId"])

        data_dir = path + "/data/"
        data_domain  = data_dir + es_info['activeDomainIndex']
        data_training = data_domain + "/training_data/"
        data_negative = data_domain + "/training_data/negative/"
        data_positive = data_domain + "/training_data/positive/"

        if (not isdir(data_positive)):
            # Create dir if it does not exist
            makedirs(data_positive)
        else:
            # Remove all previous files
            for filename in listdir(data_positive):
                remove(data_positive+filename)

        if (not isdir(data_negative)):
            # Create dir if it does not exist
            makedirs(data_negative)
        else:
            # Remove all previous files
            for filename in listdir(data_negative):
                remove(data_negative+filename)

        pos_tags = ["Relevant"]
        neg_tags = ["Irrelevant"]

        try:
            pos_tags = session['model']['positive']
        except KeyError:
            print "Using default positive tags"

        try:
            neg_tags = session['model']['negative']
        except KeyError:
            print "Using default negative tags"

        pos_docs = []

        for tag in pos_tags: #.split(','):
            s_fields = {}
            query = {
                "wildcard": {es_info['mapping']["tag"]:tag}
            }
            s_fields["queries"] = [query]

            results = multifield_term_search(s_fields,
                                             0, self._all,
                                             ["url", es_info['mapping']['html']],
                                             es_info['activeDomainIndex'],
                                             es_info['docType'],
                                             self._es)

            pos_docs = pos_docs + results['results']

        pos_html = {field['url'][0]:field[es_info['mapping']["html"]][0] for field in pos_docs}

        neg_docs = []
        for tag in neg_tags: #.split(','):
            s_fields = {}
            query = {
                "wildcard": {es_info['mapping']["tag"]:tag}
            }
            s_fields["queries"] = [query]
            results = multifield_term_search(s_fields,
                                             0, self._all,
                                             ["url", es_info['mapping']['html']],
                                             es_info['activeDomainIndex'],
                                             es_info['docType'],
                                             self._es)
            neg_docs = neg_docs + results['results']

        neg_html = {field['url'][0]:field[es_info['mapping']["html"]][0] for field in neg_docs}

        seeds_file = data_domain +"/seeds.txt"
        print "Seeds path ", seeds_file
        with open(seeds_file, 'w') as s:
            for url in pos_html:
                try:
                    file_positive = data_positive + self._encode(url.encode('utf8'))
                    s.write(url.encode('utf8') + '\n')
                    with open(file_positive, 'w') as f:
                        f.write(pos_html[url].encode('utf8'))

                except IOError:
                    _, exc_obj, tb = exc_info()
                    f = tb.tb_frame
                    lineno = tb.tb_lineno
                    filename = f.f_code.co_filename
                    linecache.checkcache(filename)
                    line = linecache.getline(filename, lineno, f.f_globals)
                    print 'EXCEPTION IN ({}, LINE {} "{}"): {}'.format(filename, lineno, line.strip(), exc_obj)

        for url in neg_html:
            try:
                file_negative = data_negative + self._encode(url.encode('utf8'))
                with open(file_negative, 'w') as f:
                    f.write(neg_html[url].encode('utf8'))
            except IOError:
                _, exc_obj, tb = exc_info()
                f = tb.tb_frame
                lineno = tb.tb_lineno
                filename = f.f_code.co_filename
                linecache.checkcache(filename)
                line = linecache.getline(filename, lineno, f.f_globals)
                print 'EXCEPTION IN ({}, LINE {} "{}"): {}'.format(filename, lineno, line.strip(), exc_obj)

        domainmodel_dir = data_domain + "/models/"

        if (not isdir(domainmodel_dir)):
            makedirs(domainmodel_dir)
        else:
            # Remove all previous files
            for filename in listdir(domainmodel_dir):
                remove(domainmodel_dir+filename)


        if len(neg_docs) > 0:
            ache_home = environ['ACHE_HOME']
            comm = ache_home + "/bin/ache buildModel -t " + data_training + " -o "+ domainmodel_dir + " -c " + ache_home + "/config/sample_config/stoplist.txt"
            p = Popen(comm, shell=True, stderr=PIPE)
            output, errors = p.communicate()
            print output
            print errors
        else:
            return "No irrelevant pages to build domain model"

        if zip:
            return self._createModelZip(session)

        return "Model created successfully"

    def createRegExModel(self, terms=[], session=None, zip=True):
        """ Create a RegEx ACHE model to be applied to SeedFinder and focused crawler.
        It saves a pageclassifier.yml in  <project>/data/<domain> directory which contains
        the regular expressions to be applied to classify the page. The regular expressions
        are generated from terms annotated or uploaded by the user if no terms are input
        to the method.

        If zip=True all generated files and folders are zipped into a file.

        Parameters:
        session (json): should have domainId

        Returns:
        Zip file url or message text
        """
        
        if len(terms) == 0:
            return "Model not created"

        path = self._path

        es_info = self._esInfo(session["domainId"])

        data_dir = path + "/data/"
        data_domain  = data_dir + es_info['activeDomainIndex']
        domainmodel_dir = data_domain + "/models/"

        if (not isdir(domainmodel_dir)):
            makedirs(domainmodel_dir)
        else:
            # Remove all previous files
            for filename in listdir(domainmodel_dir):
                remove(domainmodel_dir+filename)

        #Generate patterns from terms
        with open( domainmodel_dir+"/pageclassifier.yml", "w") as pc_f:
            pc_f.write("type: regex\nparameters:\n    boolean_operator: \"OR\"\n")

            patterns = ""
            for term in terms:
                patterns = patterns + "        - .*"+term+".*\n"

            pc_f.write("    title:\n      boolean_operator: \"OR\"\n      regexes:\n")
            pc_f.write(patterns)
            pc_f.write("    content:\n      boolean_operator: \"OR\"\n      regexes:\n")
            pc_f.write(patterns)

        if zip:
            return self._createModelZip(session)

        return "Model created successfully"


    def _createModelZip(self, session):

        """ Create a zip of generated crawler model

        Parameters:
        session (json): should have domainId

        Returns:
        Zip file url or message text
        """


        path = self._path

        es_info = self._esInfo(session["domainId"])

        data_dir = path + "/data/"

        print data_dir
        print es_info['activeDomainIndex']

        data_domain  = data_dir + es_info['activeDomainIndex']
        domainmodel_dir = data_domain + "/models/"

        zip_dir = data_dir
        #Create tha model in the client (client/build/models/). Just the client site is being exposed
        saveClientSite = zip_dir.replace('server/data/','client/build/models/')
        if (not isdir(saveClientSite)):
            makedirs(saveClientSite)
        zip_filename = saveClientSite + es_info['activeDomainIndex'] + "_model.zip"

        with ZipFile(zip_filename, "w") as modelzip:
            if (isfile(domainmodel_dir + "/pageclassifier.features")):
                print "zipping file: "+domainmodel_dir + "/pageclassifier.features"
                modelzip.write(domainmodel_dir + "/pageclassifier.features", "pageclassifier.features")

            if (isfile(domainmodel_dir + "/pageclassifier.model")):
                print "zipping file: "+domainmodel_dir + "/pageclassifier.model"
                modelzip.write(domainmodel_dir + "/pageclassifier.model", "pageclassifier.model")

            if (exists(data_domain + "/training_data/positive")):
                print "zipping file: "+ data_domain + "/training_data/positive"
                for (dirpath, dirnames, filenames) in walk(data_domain + "/training_data/positive"):
                    for html_file in filenames:
                        modelzip.write(dirpath + "/" + html_file, "training_data/positive/" + html_file)

            if (exists(data_domain + "/training_data/negative")):
                print "zipping file: "+ data_domain + "/training_data/negative"
                for (dirpath, dirnames, filenames) in walk(data_domain + "/training_data/negative"):
                    for html_file in filenames:
                        modelzip.write(dirpath + "/" + html_file, "training_data/negative/" + html_file)

            if (isfile(data_domain +"/seeds.txt")):
                print "zipping file: "+data_domain +"/seeds.txt"
                modelzip.write(data_domain +"/seeds.txt", es_info['activeDomainIndex'] + "_seeds.txt")
                chmod(zip_filename, 0o777)

            if (isfile(domainmodel_dir + "/pageclassifier.yml")):
                print "zipping file: "+domainmodel_dir + "/pageclassifier.yml"
                modelzip.write(domainmodel_dir + "/pageclassifier.yml", "pageclassifier.yml")


        return "models/" + es_info['activeDomainIndex'] + "_model.zip"

#######################################################################################################
# Run Crawler
#######################################################################################################

    def startCrawler(self, type, seeds, terms, session):
        """ Start the ACHE crawler for the specfied domain with the domain model. The
        results are stored in the same index

        Parameters:
        session (json): should have domainId

        Returns:
        None
        """

        domainId = session['domainId']

        es_info = self._esInfo(domainId)

        if self.runningCrawlers.get(domainId) is not None:
            if self.runningCrawlers[domainId].get(type) is not None:
                return self.runningCrawlers[domainId][type]['status']
        elif self.getStatus(type, session) == "RUNNING":
            self.runningCrawlers[domainId] = {type: {'domain': self._domains[domainId]['domain_name'], 'status': "Running" }}
            return "Running"

        if type == "focused":
            data_dir = self._path + "/data/"
            data_domain  = data_dir + es_info['activeDomainIndex']
            domainmodel_dir = data_domain + "/models/"
            domainoutput_dir = data_domain + "/output/"

            
            result = self.createModel(session, zip=True)
            if "No irrelevant pages to build domain model" in result:
                if len(terms) > 0:
                    result = self.createRegExModel(terms, session, zip=True)
                    if "Model not created" in result:
                        return "No regex domain model available"
                else:
                    return "No page classifier or regex domain model available"
                
            if (not isdir(domainmodel_dir)):
                return "No domain model available"

            zip_dir = data_dir
            saveClientSite = zip_dir.replace('server/data/','client/build/models/')
            zip_filename = saveClientSite + es_info['activeDomainIndex'] + "_model.zip"
            with open(zip_filename, "rb") as model_file:
                encoded_model = base64.b64encode(model_file.read())
            payload = {"crawlType": "FocusedCrawl", "seeds": [], "model":encoded_model}

            try:
                r = requests.post(self._servers["focused"]+"/startCrawl", data=json.dumps(payload))

                response = json.loads(r.text)

                print "\n\nFocused Crawler Response"
                pprint(response)
                print "\n\n"

                if response["crawlerStarted"]:
                    if self.runningCrawlers.get(domainId) is None:
                        self.runningCrawlers[domainId] = {type: {'domain': self._domains[domainId]['domain_name'], 'status': "Running" }}
                    else:
                        self.runningCrawlers[domainId][type] = {'domain': self._domains[domainId]['domain_name'], 'status': "Running" }
                    return "Running"
                else:
                    return "Failed to run crawler"

            except ConnectionError:
                print "\n\nFailed to connect to server to start crawler. Server may not be running\n\n"
                return "Failed to connect to server. Server may not be running"

        elif type == "deep":
            if len(seeds) == 0:
                return "No seeds provided"
            try:
                payload = {"crawlType": "DeepCrawl", "seeds": seeds, "model":None}
                r = requests.post(self._servers["deep"]+"/startCrawl", data=json.dumps(payload))

                response = json.loads(r.text)

                print "\n\nDeep Crawler Response"
                pprint(response)
                print "\n\n"

                if response["crawlerStarted"]:
                    if self.runningCrawlers.get(domainId) is None:
                        self.runningCrawlers[domainId] = {type: {'domain': self._domains[domainId]['domain_name'], 'status': "Running" }}
                    else:
                        self.runningCrawlers[domainId][type] = {'domain': self._domains[domainId]['domain_name'], 'status': "Running" }
                    return "Running"
                else:
                    return "Failed to run crawler"

            except ConnectionError:
                print "\n\nFailed to connect to server to start crawler. Server may not be running\n\n"
                return "Failed to connect to server. Server may not be running"

        return "Running in domain: " + self._domains[domainId]['domain_name']

    def stopCrawler(self, type, session):
        """ Stop the ACHE crawler for the specfied domain with the domain model. The
        results are stored in the same index

        Parameters:
        session (json): should have domainId

        Returns:
        None
        """

        domainId = session['domainId']

        if self.getStatus(type, session) == "RUNNING":
            try:
                r = requests.get(self._servers[type]+"/stopCrawl")

                if r.status_code == 200:
                    response = json.loads(r.text)

                    print "\n\n",type," Crawler Stop Response"
                    pprint(response)
                    print "\n\n"

                    if response["crawlerStopped"]:
                        self.crawlerStopped(type, session)
                    elif response["shutdownInitiated"]:
                        self.runningCrawlers[domainId][type]['status'] = "Terminating"
                        return "Terminating"

                elif r.status_code == 404 or r.status_code == 500:
                    return "Failed to stop crawler"

            except ConnectionError:
                print "\n\nFailed to connect to server to stop crawler. Server may not be running\n\n"
                return "Failed to connect to server. Server may not be running"

        print "\n\n\nCrawler Stopped\n\n\n"
        return "Crawler Stopped"

    def addUrls(self, seeds, session):
        domainId = session['domainId']

        if self.getStatus('deep', session) == "RUNNING":
            try:
                payload = {"seeds": seeds}
                r = requests.post(self._servers['deep']+"/seeds", data=json.dumps(payload))

                if r.status_code == 200:
                    response = json.loads(r.text)

                    print "\n\n",type," Crawler Stop Response"
                    pprint(response)
                    print "\n\n"

                elif r.status_code == 404 or r.status_code == 500:
                    return "Failed to add urls"

            except ConnectionError:
                print "\n\nFailed to connect to server to add urls. Server may not be running\n\n"
                return "Failed to connect to server. Server may not be running"

        print "\n\n\nUrls Added\n\n\n"
        return "Urls Added"


#######################################################################################################
# Crawler Status
#######################################################################################################

    def getStatus(self, type, session):

        domainId = session["domainId"]

        try:
            r = requests.get(self._servers[type]+"/status")

            print "\n\n ACHE server status response code: ", r.status_code

            if r.status_code == 200:
                response = json.loads(r.text)
            elif r.status_code == 404 or r.status_code == 500:
                try:
                    self.runningCrawlers[domainId][type]['status'] = "Failed to get status from server"
                    self.crawlerStopped(type, session)
                    return False
                except KeyError:
                    return False

        except ConnectionError:
            print "\n\nFailed to connect to server for status. Server may not be running\n\n"
            try:
                self.runningCrawlers[domainId][type]['status'] = "Failed to connect to server for status. Server may not be running"
                self.crawlerStopped(type, session)
                return False
            except KeyError:
                return False

        return response["crawlerState"]

##########################a#############################################################################
# Recommendations
#######################################################################################################

    def getRecommendations(self, num_pages, session):
        """ Method to recommend tlds for deep crawling. These are tlds in the crawled relevant pages
        which have not yet been marked for deep crawl and are sorted by the number of relevant urls
        in the tld that were crawled.

        Parameters:
        session (json): should have domainId

        Returns:
        {<tld>:<number of relevant pages crawler>}
        """

        domainId = session['domainId']

        es_info = self._esInfo(domainId)

        s_fields = {
            "tag": "Positive",
            "index": es_info['activeDomainIndex'],
            "doc_type": es_info['docType']
        }

        results = multifield_term_search(s_fields, 0, self._all, ['term'], self._termsIndex, 'terms', self._es)
        pos_terms = [field['term'][0] for field in results["results"]]

        unique_tlds = {}

        if len(pos_terms) > 0:
            mm_queries = []
            for term in pos_terms:
                mm_queries.append({'multi_match': {
                    'query': term,
                    'fields': [es_info['mapping']["text"], es_info['mapping']["title"]+"^2",es_info['mapping']["domain"]+"^3"],
                    'type': 'cross_fields',
                    'operator': 'and'
                }})
            query = {
                'query':{
                    'bool':{
                        'must_not':{
                            'term': {'isRelevant': 'irrelevant' }
                        },
                        'should': mm_queries,
                        "minimum_number_should_match": 1
                    }
                }
            }
            
            results = exec_query(query,
                                 ['url', 'domain'],
                                 0, self._all,
                                 es_info['activeDomainIndex'],
                                 es_info['docType'],
                                 self._es)
            
            domain_scored_pages = {}
            for result in results['results']:
                if result.get('domain') is None:
                    continue
                domain = result['domain'][0]
                domain_info = domain_scored_pages.get(domain)
                if domain_info is not None:
                    domain_info[0] = domain_info[0] + result['score']
                    domain_info[1] = domain_info[1] + 1
                    domain_info[2] = domain_info[0] / float(domain_info[1])
                else:
                    domain_info = [result['score'], 1, result['score']]
                    
                domain_scored_pages[domain] = domain_info

            unique_tlds = {k:{'count':v[1],'score':v[2]} for k,v in domain_scored_pages.items()}
            
        else:    
            query = {
                'bool':{
                    'must_not':{
                        'term': {'isRelevant': 'irrelevant' }
                    }
                }
            }
            for k, v in get_unique_values('domain.exact', query, self._all, es_info['activeDomainIndex'], es_info['docType'], self._es).items():
                if "." in k:
                    unique_tlds[k] = {'count':v}

        #Get tlds in pages annotated deep crawl
        query = {
            "term": {
                "tag": {
                    "value": "Deep Crawl"
                }
            }
        }

        unique_dp_tlds = {}

        for k, v in get_unique_values('domain.exact', query, self._all, es_info['activeDomainIndex'], es_info['docType'], self._es).items():
            unique_dp_tlds[k.replace("www.","")] = v

        #Get tlds that are not already annotated deep crawl
        recommendations = list(set([k.replace('www.','') for k in unique_tlds.keys()]).difference(set(unique_dp_tlds.keys())))

        recommended_tlds = {}

        for k, v in unique_tlds.items(): 
            if k in recommendations and v['count'] >= int(num_pages):
                recommended_tlds[k] = v
                
        return recommended_tlds

##########################a#############################################################################
# Manage Model Tags Settings
#######################################################################################################

    def getModelTags(self, domainId):
        """ Method to get tags to be considered positive or negative for building a model

        Parameters:
        domainId

        Returns:
        {"index": <name of index of domain>, "positive": [], "negative": []}
        """

        model_tags = get_model_tags(self._es).get(domainId)

        tags = {}
        if model_tags is not None:
            tags = {"index": model_tags["index"]}
            if  model_tags.get("positive") is not None:
                tags["positive"] =  model_tags["positive"]
            if  model_tags.get("negative") is not None:
                tags["negative"] =  model_tags["negative"]

        return tags


    def saveModelTags(self, session):
        """ Method to save tags to be considered positive or negative for building a model

        Parameters:
        session (json): should have domainId, should have {"model": {"positive": []}} to set positive tags,
                        should have {"model": {"negative": []}}  to set negative tags

        Returns:
        None
        """
        domainId = session["domainId"]

        es_info = self._esInfo(domainId)

        pos_tags = []
        try:
            pos_tags = session['model']['positive']
        except KeyError:
            print "Using default positive tags"

        neg_tags = []
        try:
            neg_tags = session['model']['negative']
        except KeyError:
            print "Using default negative tags"

        model_tags = self.getModelTags(domainId)

        entry = {
            domainId: {
                "positive": pos_tags,
                "index":  es_info["activeDomainIndex"]
            }
        }

        update_document(entry, "config", "model_tags", self._es)

        entry = {
            domainId: {
                "negative": neg_tags,
                "index":  es_info["activeDomainIndex"]
            }
        }

        update_document(entry, "config", "model_tags", self._es)
