# k8s-pod-foamtree

It's web server which uses *~/.kube/config* file for presenting resource request set for pods. It uses foamtree package for visualization. Dashboard has two option for visualization:
- cpu
- memory

Foamtree view is refreshed automaticaly every 60 seconds by default. Refresh frequency might be change by range slider.

![Drag Racing](k8s-foam-tree.png)

Node is represented by square shape box. Foams are pods. If pod contains more than one container pod foam is splited into sub-foams. Empty foam represents unused (free) resources avaiable on node.

## Memory unit
Below memory units are avaiable for displaing:
- KB
- MB
- GB
- TB

## Context
Context combobox allows for switching current k8s context. **Context is changed only in k8s-pod-foamtree web server not in ~/.kube/config file.**

## Run k8s-pod-foamtree
To run k8s-pod-foamtree run:
```
make start_server
```
**Server listen on port 8080**