# runner_wrapper
This is simple **proof of concept** project that explores how to wrap an ansible_runner invocation with a simple RESTful API. Why? Good question...

If the project you're working on isn't native python, but you can issue an OS
command, you can use this code to integrate with ansible_runner and query
progress and state during the ansible playbook execution. This could be useful in the context of a non-python GUI for example.

## Further Reading  
For further information relating to ansible_runner, look at the docs
https://ansible-runner.readthedocs.io/en/latest/index.html

## Installation
1. Install ansible_runner (see docs for all installation options)
```
> pip install ansible_runner  
```  
2. Untar this code, and ```cd``` to it  

## The API endpoints  

| endpoint | Type | Description |  
|----------|------|-------------|  
| getStatus | GET | Shows whether the playbook is running or not |  
| getActiveTask | GET | Return the name of the currently active task in the playbook |  
| getTasks | GET | Return a list of tasks. Each task is defined by a task_uuid, task, and host |  
| getTaskInfo | GET | With the task info from getTasks you can query any variable within the 'res' field of the ansible task (i.e. it's result, like 'rc') |  
| shutdown | POST | once the playbook is complete, the wrapper will wait for a shutdown request (timeout is 5mins) |  


## Testing
The archive provides a simple multi-task playbook, where each step is just a ```sleep``` command. To test the interaction, simply run the ```runner_wrapper.py``` module, and use ```curl``` from another shell to query the state of the playbook.  

e.g.  
```
> python runner_wrapper.py  
```  

Then from another shell window;  
```
curl -XGET http://localhost:8080/getStatus  
{"status": "running"}  

[paul@rh460p tmp]$ curl -XGET http://localhost:8080/getActiveTask  
{"active_task": "Step 1"}  

curl -XGET http://localhost:8080/getTasks  
{"taskList": [{"task_uuid": "c85b7671-906d-be82-f0e2-000000000007", "task": "Step 1", "host": "localhost"}]}  

[paul@rh460p tmp]$ curl -XGET http://localhost:8080/getActiveTask  
{"active_task": "Step 2"}  

curl -XGET 'http://localhost:8080/getTaskInfo?task_uuid=c85b7671-906d-be82-f0e2-000000000007&task=Step%201&host=localhost&var=rc'  
{"data": 0}  

curl -XPOST http://localhost:8080/shutdown
```  

## Additional Information  
For this POC code, I also modified ansible_runner itself to reduce the output that gets written to the console during a run. This was a simple change just touching ;  

- the ```OutputEventFilter``` method
- the Runner ```__init__``` method, adding a quiet variable that defaults to ```False```  

With these changes in place the console output looks like this;  
```
[paul@rh460p ansible-runner]$ python runner_wrapper.py
Not loading passwords
Not loading environment vars
Not loading extra vars
Not loading settings
Not loading ssh key
2018-06-18 14:50:29,964 - __main__ - DEBUG - ansible runner thread started
2018-06-18 14:50:29,964 - __main__ - DEBUG - http REST endpoint started
2018-06-18 14:50:29,965 - __main__ - INFO - Waiting for playbook to complete
2018-06-18 14:50:30,542 - __main__ - DEBUG - Running task 'Step 1'
2018-06-18 14:50:40,812 - __main__ - DEBUG - Running task 'Step 2'
2018-06-18 14:50:51,033 - __main__ - DEBUG - Running task 'Step 3'
2018-06-18 14:51:01,253 - __main__ - DEBUG - Running task 'Step 4'
2018-06-18 14:51:11,479 - __main__ - DEBUG - Running task 'Step 5'
2018-06-18 14:51:21,706 - __main__ - DEBUG - Running task 'Step 6'
2018-06-18 14:51:31,933 - __main__ - DEBUG - Running task 'Step 7'
2018-06-18 14:51:42,548 - __main__ - INFO - Playbook finished
2018-06-18 14:51:42,549 - __main__ - INFO - - status: successful
2018-06-18 14:51:42,549 - __main__ - INFO - - rc: 0
2018-06-18 14:51:42,550 - __main__ - DEBUG - Task names processed : <STARTED>,Step 1,Step 2,Step 3,Step 4,Step 5,Step 6,Step 7,<ENDED>
2018-06-18 14:51:42,550 - __main__ - INFO - Waiting for client to signal post-run shutdown (timeout=300s)
2018-06-18 14:51:48,323 - __main__ - DEBUG - /shutdown requested from 127.0.0.1
2018-06-18 14:51:48,558 - __main__ - INFO - ansible runner api shutting down  
```
