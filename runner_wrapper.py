#!/usr/bin/env python2

import logging.config
import threading
import urlparse
import logging
import codecs
import time
import json
import yaml
import sys
import os
import re

from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
from SocketServer import ThreadingMixIn

from ansible_runner import Runner, RunnerConfig
from ansible_runner.utils import to_artifacts


DEFAULT_PORT = 8080
TIMEOUT = 300

# Additional changes made to ansible_runner code for this POC
# to prevent console traffic (optional)
#   - added a quiet mode to OutputEventFilter
#   - add quiet parameter to the Runner __init__ defaulting to False


class MyRunner(Runner):

    def __init__(self, *args, **kwargs):
        self.active_tasks = []
        Runner.__init__(self, *args, **kwargs)

    @property
    def current_task(self):
        return self.active_tasks[-1]

    def event_callback(self, event_data):
        """
        Invoked for every Ansible event to collect stdout with the event data
        and store it for later use
        """
        stdout = event_data.get('stdout', None)

        if stdout.startswith('\r\nTASK'):
            task_description = re.search(r'\[(.*)\]', stdout).group(1)
            logger.debug("Running task '{}'".format(task_description))
            self.active_tasks.append(task_description)
        elif stdout.startswith('\r\nPLAY RECAP'):
            self.active_tasks.append("<ENDED>")
        elif stdout.startswith('\r\nPLAY '):
            self.active_tasks.append("<STARTED>")

        if 'uuid' in event_data:
            filename = '{}-partial.json'.format(event_data['uuid'])
            partial_filename = os.path.join(self.config.artifact_dir,
                                            'job_events',
                                            filename)
            full_filename = os.path.join(self.config.artifact_dir,
                                         'job_events',
                                         '{}-{}.json'.format(
                                             event_data['counter'],
                                             event_data['uuid']))
            try:
                with codecs.open(partial_filename, 'r', encoding='utf-8') as \
                        read_file:
                    partial_event_data = json.load(read_file)
                event_data.update(partial_event_data)
                with codecs.open(full_filename, 'w', encoding='utf-8') as \
                        write_file:
                    json.dump(event_data, write_file)

                if self.remove_partials:
                    os.remove(partial_filename)
            except IOError as e:
                print("Failed writing event data: {}".format(e))


def my_async(**kwargs):
    to_artifacts(kwargs)
    rc = RunnerConfig(**kwargs)
    rc.prepare()
    r = MyRunner(rc)
    runner_thread = threading.Thread(target=r.run, args=(True,))
    return runner_thread, r


class Handler(BaseHTTPRequestHandler):
    """ HTTP request handler """
    quiet = True

    valid_routes = ['/getActiveTask',
                    '/getTaskInfo',
                    '/getTasks',
                    '/getStatus']

    ansible_runner = None
    shutdown_request = False

    @classmethod
    def playbook_complete(cls):
        cls.shutdown_request = True

    def log_message(self, format_str, *args):
        """ write a log message for all successful requests """
        if Handler.quiet:
            pass
        else:
            # default way of handling successful requests
            msg_template = format_str.replace('%s', '{}')

            sys.stderr.write("{} - - [{}] {}\n".format(
                                                  self.address_string(),
                                                  self.log_date_time_string(),
                                                  msg_template.format(*args)))

    def get_active_task(self):
        """
        just show the task name of the currently executing task
        """
        data = json.dumps({"active_task": self.ansible_runner.current_task})

        self.send_json(data)

    def get_tasks(self):
        """
        Return a json tasklist. Each item in the list contains task_uuid,
        task and host which can then be used by the getTaskInfo call.

        e.g.
        {"taskList": [{"task_uuid": "c85b7671-906d-2927-7ad5-000000000007",
                       "task": "Step 1", "host": "localhost"},
                       {"task_uuid": "c85b7671-906d-2927-7ad5-000000000008",
                       "task": "Step 2", "host": "localhost"},
                       {"task_uuid": "c85b7671-906d-2927-7ad5-000000000009",
                       "task": "Step 3", "host": "localhost"}
                     ]
        }
        """
        tasks = []
        for ev in self.ansible_runner.events:
            if not ev.get('event').startswith('runner_on'):
                continue

            event_data = ev.get('event_data')
            tasks.append({"task": event_data.get('task'),
                          "task_uuid": event_data.get('task_uuid'),
                          "host": event_data.get('host')})

        data = json.dumps({'taskList': tasks})
        self.send_json(data)

    def get_status(self):
        """ Return the status of the playbook: running, completed """

        data = json.dumps({'status': self.ansible_runner.status})
        self.send_json(data)

    def send_json(self, data, status_code=200):
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(data)
        self.wfile.write('\n')

    def get_task_info(self):
        """ return a specific variable from the results output of a task """

        qvars = urlparse.parse_qs(urlparse.urlparse(self.path).query)
        logger.debug("{}".format(qvars))
        if len(qvars.keys()) != 4 or \
           any([key not in ['task', 'task_uuid', 'host', 'var']
                for key in qvars]):
            logger.warning("/getTaskInfo missing required variables")
            self.send_error(400,
                            message="taskInfo needs task,task_uuid,host "
                                    "and a variable name")
            return

        for ev in self.ansible_runner.events:
            event_data = ev.get('event_data')

            if 'task_uuid' not in event_data:
                continue

            var = qvars.get('var')[0]

            if event_data.get('host') == qvars.get('host')[0] and \
               event_data.get('task') == qvars.get('task')[0] and \
               event_data.get('task_uuid') == qvars.get('task_uuid')[0]:
                logger.debug("/getTaskInfo match found on variables passed")
                if var in event_data.get('res'):
                    logger.debug("/getTaskInfo variable found within event")
                    data = json.dumps({"data": event_data['res'].get(var)})
                    self.send_json(data)
                    return
                else:
                    # variable requested not found, but tasks exists
                    logger.warning("/getTaskInfo event found, but matching "
                                   "variable not present in event data - "
                                   "requester "
                                   "{}".format(self.client_address[0]))
                    self.send_error(404,
                                    message="Task exists, variable doesn't "
                                            "in results(res)")
                    return

        # at this point the request didn't find a valid playbook event
        logger.error("/getTaskInfo had variables set, but a match couldn't"
                     "be found in the events directory")
        self.send_error(404,
                        message="Task not found")

    def do_GET(self):
        """ Basic GET request handler """

        path = self.path.split('?')
        rpath = path[0]
        logger.debug("GET request full path is {}".format(self.path))
        if rpath not in Handler.valid_routes:
            logger.warning("Invalid GET request from requester "
                           "({})".format(self.client_address[0]))
            self.send_error(404,
                            message="Undefined endpoint")
            return

        if rpath == '/getActiveTask':
            self.get_active_task()

        elif rpath == '/getTasks':
            self.get_tasks()

        elif rpath == '/getStatus':
            self.get_status()

        elif rpath == '/getTaskInfo':

            if len(path) > 1:
                self.get_task_info()
            else:
                logger.warning("/getTaskInfo requested without variables!")
                self.send_error(400,
                                message="Variables missing from taskInfo "
                                        "query")
                return

    def do_POST(self):
        """ Handle POST requests """
        if self.path == '/shutdown':
            if self.ansible_runner.status in ['starting', 'running']:
                logger.warning("/shutdown requested, but the playbook is "
                               "still running...request ignored "
                               "({})".format(self.client_address[0]))
                self.send_error(400,
                                message="Can't shutdown until playbook is "
                                        "complete")
            else:
                # playbook has finished
                self.send_response(200)
                logger.debug("/shutdown requested from "
                             "{}".format(self.client_address[0]))
                Handler.playbook_complete()


class PlaybookAPI(ThreadingMixIn, HTTPServer):
    """Basic multi-threaded HTTP server"""
    # stop = False


def main():

    run_thread, run_object = my_async(private_data_dir='./ansible',
                                      # inventory='localhost',
                                      playbook='test.yml')

    endpoint = PlaybookAPI(('0.0.0.0', DEFAULT_PORT),
                           Handler)

    endpoint.RequestHandlerClass.ansible_runner = run_object

    endpoint_thread = threading.Thread(target=endpoint.serve_forever)
    endpoint_thread.daemon = True

    run_thread.start()
    logger.debug("ansible runner thread started")
    if run_object.status == 'starting':
        logger.debug("http REST endpoint started")
        endpoint_thread.start()
    else:
        logger.error("start of ansible runner thread failed")
        sys.exit(8)

    logger.info("Waiting for playbook to complete")
    while run_thread.is_alive():    # keep main thread alive while the
                                    # playbook runs
        time.sleep(0.5)

    logger.info("Playbook finished")
    logger.info("- status: {}".format(run_object.status))
    logger.info("- rc: {}".format(run_object.rc))
    logger.debug("Task names processed : {}".format(','.join(
                                                    run_object.active_tasks)))
    logger.info("Waiting for client to signal post-run shutdown "
                "(timeout={}s)".format(TIMEOUT))

    end = time.time() + TIMEOUT
    while not endpoint.RequestHandlerClass.shutdown_request:
        time.sleep(0.5)
        current = time.time()
        if current >= end:
            logger.info("Timed out waiting for /shutdown call from client")
            break

    if endpoint.RequestHandlerClass.shutdown_request:
        logger.info("ansible runner api shutting down")


def setup_logging(default_path='logging.yaml',
                  default_level=logging.INFO,
                  env_key='LOG_CFG'):
    """ Setup logging using yaml definition """

    path = default_path
    value = os.getenv(env_key, None)
    if value:
        path = value
    if os.path.exists(path):
        with open(path, 'rt') as f:
            config = yaml.safe_load(f.read())
        logging.config.dictConfig(config)
    else:
        logging.basicConfig(level=default_level)


if __name__ == '__main__':

    logger = logging.getLogger(__name__)

    setup_logging()

    main()
