import urllib2
import sys
from os import environ, chdir
from elastic.config import es_server

from subprocess import Popen, PIPE, STDOUT

def encode( url):
  return urllib2.quote(url).replace("/", "%2F")

def decode( url):
  return urllib2.unquote(url).replace("%2F", "/")

def validate_url( url):
  s = url[:4]
  if s == "http":
    return url
  else:
    url = "http://" + url
  return url
  
def get_downloaded_urls(inputfile):
  urls = []
  with open(inputfile, 'r') as f:
    urls = f.readlines
  urls = [url.strip() for url in urls]
  return urls

def download(inputfile, es_index = "memex", es_doc_type = "page", es_host="http://localhost"):
  parts = es_host.split(':')
  if len(parts) == 2:
    es_host = parts[0]
  elif len(parts) == 3:
    es_host = parts[1]

  es_host = es_host.strip('/')

  print es_host

  query = ""
  with open('conf/queries.txt', 'r') as f:
    for line in f:
      query = line.strip();

  comm = "java -cp target/seeds_generator-1.0-SNAPSHOT-jar-with-dependencies.jar Download " \
         + inputfile + ' "' + query +'" ' + es_index + " " + es_doc_type + " " + es_host;

  print comm

  p=Popen(comm, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT)
  # output, errors = p.communicate()
  # print output
  # if not (errors == None):
  #   print '*' * 80, '\n\n\n'  
  #   print errors

def callDownloadUrls(query, subquery, urls_str, es_info):

  chdir(environ['DD_API_HOME']+'/seeds_generator')
  
  # Download 100 urls at a time
  step =  100
  urls = urls_str.split(" ")
  url_size = 0
  num_pages = 0
  if len(urls) >= step:
    for url_size in range(0, len(urls), step):
      comm = "java -cp target/seeds_generator-1.0-SNAPSHOT-jar-with-dependencies.jar Download_urls -q \"" + query + "\" -u \"" + " ".join(urls[url_size:url_size + step]) + "\"" \
             " -i " + es_info['activeDomainIndex'] + \
             " -d " + es_info['docType'] + \
             " -s " + es_server
      
      if subquery is not None:
        comm = comm + " -sq \"" + subquery + "\""
      
      p=Popen(comm, shell=True, stdout=PIPE)
      output, errors = p.communicate()
      print output
      print errors
      
  if len(urls[url_size:]) < step:
    comm = "java -cp target/seeds_generator-1.0-SNAPSHOT-jar-with-dependencies.jar Download_urls -q \"" + query + "\" -u \"" + " ".join(urls[url_size:]) + "\"" \
           " -i " + es_info['activeDomainIndex'] + \
           " -d " + es_info['docType'] + \
           " -s " + es_server
    
    if subquery is not None:
      comm = comm + " -sq \"" + subquery + "\""

    p=Popen(comm, shell=True, stdout=PIPE)
    output, errors = p.communicate()
    
    print "\n\n\n", output, "\n\n\n"
    print "\n\n\n", errors, "\n\n\n"

def main(argv):
  if len(argv) != 1:
    print "Invalid arguments"
    print "python download.py inputfile"
    return
  inputfile=argv[0]
  
  download(inputfile)

if __name__=="__main__":
  main(sys.argv[1:])
