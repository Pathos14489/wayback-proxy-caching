from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, Response
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import json
import os
import random
import uuid
import datetime
import requests
import threading
import time
import zipfile
from urllib import parse
from tqdm import tqdm
import re
import asyncio
host_address = "192.168.1.101"

def get_spine_key(book):
    spine_keys = {id:(ii,id) for (ii,(id,show)) in enumerate(book.spine)}
    past_end = len(spine_keys)
    return lambda itm: spine_keys.get(itm.get_id(), (past_end,itm.get_id()))

def get_spine_items(book):
    return sorted([get_spine_key(book)(itm) for itm in book.get_items()])

def latency(func, *args, **kwargs):
    def wrapper(self, *args, **kwargs):
        start = time.time()
        func(self, *args, **kwargs)
        end = time.time()
        print(f"Function '{func.__name__}' took {end-start} seconds to run.")
    return wrapper

class WaybackCachingProxy:
    def __init__(self, timestamp:int = 20141010, worker_time:int = 4,day_month_sync: bool = False,
            eras = [
                {
                    "name": "version_2",
                    "start_range_timestamp": 1319474544000
                }
            ],
            app: FastAPI = None,
            templates: Jinja2Templates = None,
            worker: bool = False,
            fimfarchive_file_path: str = None,
        ): # Default timestamp is 2014-03-27
        self.base_timestamp = timestamp
        self.day_month_sync = day_month_sync
        self.work_queue = []
        self.worker_time = worker_time
        self.fast_api_app = app
        self.fast_api_templates = templates
        self.cache_dir = "./cache"
        self.wayback_lock = asyncio.Lock()
        self.user_agent = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1 Ddg/17.6",
        }
        # self.post_request_delay = 0.5
        self.post_request_delay = 1
        # self.post_request_delay = 6
        self.session = requests.Session()

        self.worker_thread = None
        if worker:
            self.worker_thread = self.start_worker() # Runs every 5 seconds by default, checks for work, does work, then resumes checking for work

        self.default_cache_length = 30 # 1 month~ in days

        self.error_list = []
        with open("error_list", "r") as f:
            self.error_list = f.read().split("\n")
            self.error_list = [error for error in self.error_list if error != ""]
        self.ad_list = []
        with open("ad_list", "r") as f:
            self.ad_list = f.read().split("\n")
            self.ad_list = [ad for ad in self.ad_list if ad != ""]
        
        print("Wayback Caching Proxy Time:",datetime.datetime.fromtimestamp(self.timestamp))

    def start_worker(self):
        return threading.Thread(target=self.worker, daemon=True).start()

    @property
    def timestamp(self):
        """Get the current timestamp of the Wayback Caching Proxy instance"""
        # YYYYMMDDhhmmss
        timestamp_string = str(self.base_timestamp)
        year = int(timestamp_string[:4])
        month = int(timestamp_string[4:6])
        day = int(timestamp_string[6:8])
        # Get system time
        current_timestamp = datetime.datetime.now()
        if self.day_month_sync:
            month = current_timestamp.month
            day = current_timestamp.day
        return datetime.datetime(year, month, day).timestamp()
    
    @property
    def year(self):
        """Get the year of the Wayback Caching Proxy instance"""
        return datetime.datetime.fromtimestamp(self.timestamp).year
    
    @property
    def wayback_timestamp(self):
        """Get the Wayback Machine timestamp of the Wayback Caching Proxy instance"""
        timestamp = self.timestamp
        date = datetime.datetime.fromtimestamp(timestamp)
        return date.strftime("%Y%m%d%H%M%S")
    
    def add_to_error_list(self, url):
        with open("error_list", "a") as f:
            f.write(url + "\n")
        self.error_list.append(url)

    def add_to_ad_list(self, url):
        with open("ad_list", "a") as f:
            f.write(url + "\n")
        self.ad_list.append(url)
    
    async def get_html(self, internet_file_path, url):
        if url.startswith("https://web.archive.org/web/"):
            url = url.replace("https://web.archive.org/web/","")
        if url in self.error_list:
            return "Error: This page could not be loaded. It may have been removed from the Wayback Machine or is not available at this time.", 404
        if not os.path.exists(internet_file_path): # if the file doesn't exist, generate it
            print("Caching HTML from Wayback:", url)
            async with self.wayback_lock:
                request_url = f"https://web.archive.org/web/{self.wayback_timestamp}id_/{url}"
                print("Caching HTML:", request_url)
                req = None 
                while req is None:
                    try:
                        # req = requests.get(request_url, heders=self.user_agent)
                        req = self.session.get(request_url, headers=self.user_agent)
                    except Exception as e:
                        print("Error:",e)
                    time.sleep(self.post_request_delay)
                if req.status_code != 200:
                    self.add_to_error_list(url)
                    return "Error: This page could not be loaded. It may have been removed from the Wayback Machine or is not available at this time.", 404
                raw_html = req.text
                while "https://" in raw_html: # replace https with http to prevent mixed content errors
                    raw_html = raw_html.replace("https://","http://") # replace https with http to prevent mixed content errors
                if raw_html.startswith("ï»¿"): # remove BOM from the beginning of the file
                    raw_html = raw_html[3:]
                with open(internet_file_path, "w", encoding="utf-8") as f:
                    f.write(raw_html)
        else: # if the file exists, read it
            print("Reading HTML from file:", internet_file_path)
            with open(internet_file_path, "r", encoding="utf-8") as f:
                raw_html = f.read()
        return raw_html, 200

    async def get_file(self, internet_file_path, url):
        if url.startswith("https://web.archive.org/web/"):
            url = url.replace("https://web.archive.org/web/","")
        if url in self.error_list:
            return "Error: This file could not be loaded. It may have been removed from the Wayback Machine or is not available at this time.", 404
        if not os.path.exists(internet_file_path):
            async with self.wayback_lock:
                request_url = f"https://web.archive.org/web/{self.wayback_timestamp}id_/{url}"
                print("Caching file:", request_url)
                req = None 
                while req is None:
                    try:
                        # req = requests.get(request_url, headers=self.user_agent)
                        req = self.session.get(request_url, headers=self.user_agent)
                    except Exception as e:
                        print("Error:",e)
                        req = None
                        time.sleep(self.post_request_delay)
                    time.sleep(self.post_request_delay)
                if req.status_code != 200:
                    self.add_to_error_list(url)
                    return "Error: This file could not be loaded. It may have been removed from the Wayback Machine or is not available at this time.", 404
                raw_file = req.content
                with open(internet_file_path, "wb") as f:
                    f.write(raw_file)
        else:
            with open(internet_file_path, "rb") as f:
                raw_file = f.read()
        return raw_file, 200

    def get_url_info(self, url, parameters, request_accepts, req_year, req_month, req_day): # Convert URL to Path info - Example: https://www.google.com/ -> ./internet/com/google/index.html, ./internet/com/google/, index.html, html - Example 2: https://www.google.com/search?q=hello -> ./internet/com/google/search/index.html, ./internet/com/google/search/, index.html, html
        print("URL:", url)
        if url.startswith("https://web.archive.org/web/"):
            url = url.replace("https://web.archive.org/web/","") # remove the server url from the beginning of the url if it's there
        if url.startswith("/"):
            url = url[1:]
        assert (url.startswith("http://") or url.startswith("https://")), "URL must start with http:// or https://"

        path_chunks = url.replace("http://","").replace("https://","").split("/")
        domain_parts = path_chunks.pop(0).split(".")
        domain_parts = ["domain-"+part for part in domain_parts]
        domain_parts[-1] = "tld-" + domain_parts[-1].replace("domain-","")
        domain_parts.reverse()

        path_parts = []
        for part in domain_parts:
            path_parts.append(part)
        path_parts += path_chunks
        path_parts = [part for part in path_parts if part != ""] # remove empty parts
        path_parts = [part.replace(",","").replace(";","").replace(":","").replace("%","").replace("?","").replace("&","").replace("=","").replace("+","").replace("#","").replace("@","") for part in path_parts] # remove special characters
        print("PATH PARTS:", path_parts)

        most_recent_date = datetime.datetime(req_year, req_month, req_day)
        cached_dates = []
        for year in os.listdir(self.cache_dir):
            if year.isdigit():
                for month in os.listdir(os.path.join(self.cache_dir, year)):
                    if month.isdigit():
                        for day in os.listdir(os.path.join(self.cache_dir, year, month)):
                            if day.isdigit():
                                relative_path = []
                                for part in path_parts:
                                    if part == "http://www" or part == "https://www" or part == "www": # skip www to simulate a real website using www to mirror the non-www version
                                        continue
                                    relative_path.append(part)
                                relative_path = "/".join(relative_path)
                                if os.path.exists(os.path.join(self.cache_dir, year, month, day, relative_path)):
                                    cached_dates.append(datetime.datetime(int(year), int(month), int(day)))
        if len(cached_dates) > 0:
            if max(cached_dates) > most_recent_date - datetime.timedelta(days=self.default_cache_length) and max(cached_dates) < most_recent_date:
                print("Found recent cache:", max(cached_dates))
                most_recent_date = max(cached_dates)

        internet_file_path = [self.cache_dir, str(most_recent_date.year), str(most_recent_date.month), str(most_recent_date.day)]
        config_file_path = []
        for part in path_parts:
            # print("PART:", part)
            if part == "http://www" or part == "https://www" or part == "www": # skip www to simulate a real website using www to mirror the non-www version
                continue
            internet_file_path.append(part)
            config_file_path.append(part)
        internet_file_path = "/".join(internet_file_path)
        internet_file_path = internet_file_path.replace("http://","").replace("https://","").replace(":8080","")
        config_file_path = "/".join(config_file_path)
        config_file_path = config_file_path.replace("http://","").replace("https://","").replace(":8080","")
        
        # Make sure the path would lead to a file, assume index.html if it's not, then make sure the path exists in the internet folder on the server
        # print("INTERNET FILE PATH:", internet_file_path) # Example :{self.cache_dir}com/google/
        if internet_file_path[-1] == "/": # if the path ends with a slash, assume index.html
            if request_accepts == "text/html":
                # print("html1")
                internet_file_path += "index.html"
            else:
                internet_file_path += "index."+request_accepts.split("/")[1]
        else:
            # if internet_file_path.endswith("index.html") or internet_file_path.endswith("index.htm"): # if the path ends with index.html or index.htm, remove it
            #     internet_file_path = internet_file_path[:-10]
            dot_count = internet_file_path.count(".")
            if dot_count <= 1: # if the path doesn't have a file extension, assume index.html
                if request_accepts == "text/html":
                    # print("html2")
                    internet_file_path += "/index.html"
                else:
                    internet_file_path += "."+request_accepts.split("/")[1]

        # Get the directory path and filename from the internet_file_path
        internet_dir_path = "/".join(internet_file_path.split("/")[:-1])
        if internet_dir_path[-1] != "/":
            internet_dir_path += "/"
        filename = internet_file_path.split("/")[-1]
        # Determine the file_type of the path by the filename extension
        file_type = "file"
        image_filetypes = ["png","jpg","jpeg","gif"] # supported image filetypes
        file_extension = filename.split(".")[-1]
        if file_extension == "html" or file_extension == "php":
            file_type = "html" # html file
        if file_extension in image_filetypes:
            file_type = "image" # image file
        elif file_extension == "css":
            file_type = "css"
        # elif file_extension == "html":
        #     file_type = "html" # html file
        # elif file_extension == "js":
        #     file_type = "js"
        # else:
        #     file_type = "file"
        parameters = parameters.split("&")
        for parameter in parameters:
            if parameter == "":
                continue
            parameter = parameter.replace("=","--")
            filename += "." + parameter
            internet_file_path += "." + parameter

            
        return internet_file_path, internet_dir_path, filename, file_type, file_extension # example:{self.cache_dir}com/google/index.html, {self.cache_dir}com/google/, index.html, html
    
    def worker(self):
        while True:
            try:
                time.sleep(self.worker_time + random.randint(5, 10)) # Sleep for worker_time + 5-10 seconds, offset to prevent all workers from running at the same time when running more than one worker
                print("Worker checking for work...")
                # Chec work_queue for work and do a task if there is anything in the queue
                if len(self.work_queue) > 0:
                    # print("Worker found work in queue.")
                    work = self.work_queue.pop(0)
                    print("Worker doing work:",work, "| Work Left:",len(self.work_queue))
                    # Do work based on the work["type"]
                    raise ValueError("Unknown work type: " + work["type"] + ". Discarding invalid work.")
            except Exception as e:
                print("Worker error:",e)

        

app = FastAPI()

# app.add_middleware(HTTPSRedirectMiddleware)

# app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="./templates")

with open("timestamp") as f:
    timestamp = int(f.read())
    print("Loaded timestamp from file:",timestamp)

waycache = WaybackCachingProxy(timestamp, day_month_sync=True, app=app, templates=templates, worker=False)

# GLOBAL ROUTES - These are the same for all versions of the site. Typically these should be control panels, information pages, shared APIs, etc.

# TEMPLATE
# @app.get("/")
# def home(request: Request):
#     search_query = ""
#     if "q" in request.query_params:
#         search_query = request.query_params["q"]
#     return templates.TemplateResponse("index.html", context={"request": request, "search_query": search_query})


can_change_time = False

@app.post("/set_timestamp")
def set_timestamp(request: Request):
    global can_change_time
    timestamp = int(request.query_params["timestamp"])
    if can_change_time:
        waycache.base_timestamp = timestamp
        return {"success": True}
    return {"success": False}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# @app.route("/")
# async def index(request: Request):
#     return "Hello, World!"


# Catch-all route for all other paths
@app.get('/')
@app.get("/{path:path}") 
@app.get("/{path:path}/")
@app.get("{path:path}")
async def catch_all(path: str, request: Request):
    path = str(request.url)
    host_address_string = f"http://{host_address}:8002/"
    if path.startswith(host_address_string):
        path = path[len(host_address_string):]
    if path.startswith("http:/"):
        path = path.replace("http:/","")
    if path.startswith("https:/"):
        path = path.replace("https:/","")
    while path.startswith("/"):
        path = path[1:]
    path = "http://"+path
    print(path)
    waycache_date = datetime.datetime.fromtimestamp(waycache.timestamp)
    req_headers = dict(request.headers)
    # year = int(path)
    year = waycache_date.year
    month = waycache_date.month
    day = waycache_date.day
    if path.strip() != "":
        invalid_path = False
        for ad in waycache.ad_list:
            if path.startswith(ad):
                invalid_path = True
                break
        if invalid_path:
            return "Error: This page could not be loaded. It may have been removed from the Wayback Machine or is not available at this time.", 404
        print("Path:", path)
        # reject favicon requests
        if path == "favicon.ico":
            return ""
        # if path starts with http://localhost:8080/, remove that part
        if path.startswith("https://web.archive.org/web/"):
            path = path.replace("https://web.archive.org/web/","")
        # if path starts with internet/, remove that part
        if path.startswith("internet/"):
            path = path.replace("internet/","")
        
        alt_text = "" 
        if "?alt=" in path:
            alt_text = path.split("?alt=")[-1].split("&")[0]
            alt_text = parse.unquote_plus(alt_text)
            print("Alt text:", alt_text)
        if len(path.split("?")) > 1:
            path, parameters = path.split("?",1)
        else:
            parameters = ""

        full_url = str(request.url)
        if full_url.startswith(host_address_string):
            full_url = full_url[len(host_address_string):]
        if full_url.startswith("http:/"):
            full_url = full_url.replace("http:/","")
        if full_url.startswith("https:/"):
            full_url = full_url.replace("https:/","")
        while full_url.startswith("/"):
            full_url = full_url[1:]
        full_url = "http://"+full_url

        url = path
        print("URL:", url)

        request_accepts = req_headers.get("accept","text/html").split(",")[0].split(";")[0]
        print("Accept:", request_accepts)

        internet_file_path, internet_dir_path, filename, file_type, file_extension = waycache.get_url_info(path, parameters, request_accepts, year, month, day)

        print("INTERNET FILE PATH:", internet_file_path) # Example : ./internet/com/google/
        print("INTERNET DIR PATH:", internet_dir_path) # Example : ./internet/com/google/index.html
        print("FILE TYPE:", file_type) # Example : html, image
        print("INTERNET FILENAME:", filename) # Example : index.html

        if file_type == "image" and request_accepts == "text/html":
            request_accepts = "image/"+file_extension
        elif file_type == "css" and request_accepts == "text/html":
            request_accepts = "text/css"
        print("Response Content:", request_accepts)

        if not os.path.exists(internet_dir_path):
            os.makedirs(internet_dir_path, exist_ok=True)

        # input("Press Enter to continue...")
        try:
            if file_type == "html" and request_accepts == "text/html":
                print("Getting HTML")
                content, response_code = await waycache.get_html(internet_file_path, full_url)
                return HTMLResponse(content, status_code=response_code, headers={"Content-Type": request_accepts})
            else:
                print("Getting Generic File")
                content, response_code = await waycache.get_file(internet_file_path, full_url)
                return FileResponse(internet_file_path, status_code=response_code, headers={"Content-Type": request_accepts})
        except Exception as e:
            print("Error:",e)
            return "Error: This file could not be loaded. It may have been removed from the Wayback Machine or is not available at this time.", 404
        # elif file_type == "image":
        #     content = await waycache.get_media(internet_file_path, full_url)
        #     return FileResponse(internet_file_path, media_type="image/png")
        # elif file_type == "css":
        #     content = await waycache.get_css(internet_file_path, full_url)
        #     return Response(content, media_type="text/css")
        # elif file_type == "js":
        #     content = await waycache.get_js(internet_file_path, full_url)
        #     return Response(content, media_type="application/javascript")

@app.post("/")
@app.post("/{path:path}/")
@app.post("{path:path}")
async def post_catch_all(path: str, request: Request):
    print("POST request received.")
    path = str(request.url)
    print("Path:", path)
    print("Headers:", dict(request.headers))
    print("Body:", await request.body())
    return "POST request received."

# @app.get("/proxy.pac")
# async def proxy_pac():
#     return FileResponse("proxy.pac")


# Run the server
uvicorn.run(app, host=host_address, port=8002)