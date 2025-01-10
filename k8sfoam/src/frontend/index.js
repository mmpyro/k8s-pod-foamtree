$(document).ready(function () {
    const resourceTypes = {
        cpu: 'CPU',
        memory: 'Memory'
    };
    const memoryUnits = {
        KB: 'KB',
        MB: 'MB',
        GB: 'GB',
        TB: 'TB',
    };
    const baseUrl = window.location.origin;
    let foamtree = null;
    let resourceType = resourceTypes.cpu;
    let memoryUnit = memoryUnits.MB;
    let refreshInterval = 60;
    let worker = null;
    let context = sessionStorage.getItem('context');

    function convertMemory(memory, memoryUnit) {
        switch(memoryUnit) {
            case memoryUnits.KB:
                return `${memory.toFixed(0)} [${memoryUnit}]`;
            case memoryUnits.MB:
                return `${(memory / 1024).toFixed(0)} [${memoryUnit}]`;
            case memoryUnits.GB:
                return `${(memory / 1024 / 1024).toFixed(2)} [${memoryUnit}]`;
            case memoryUnits.TB:
                return `${(memory / 1024 / 1024 / 1024).toFixed(2)} [${memoryUnit}]`;
        }
    }

    function onGroupHover(event) {
        let selectedFoam = {
            label: null,
            weight: null
        };

        if(event.group !== null && event.group !== undefined) {
            selectedFoam.label = event.group.label;
            selectedFoam.weight = event.group.weight;
        }

        if(selectedFoam.label !== null && selectedFoam.weight !== null) {
            let content = '';
            if(resourceType === resourceTypes.cpu) {
                content = `${selectedFoam.label}, ${resourceType}: ${selectedFoam.weight}`; 
            } else {
                content = `${selectedFoam.label}, ${resourceType}: ${convertMemory(selectedFoam.weight, memoryUnit)}`; 
            }
            $('#k8sSelectedItem').text(content);
        }
    }

    function getResources(resourceType) {
        let url;
        if(context === null) {
            url = `${baseUrl}/resources/${resourceType}`;
        } else {
            url = `${baseUrl}/resources/${resourceType}?context=${context}`;
        }
        $('#k8sTitle').text(`K8s ${resourceType.toUpperCase()} request resources`);
        $.get(url, function(data, status){
            if(status === 'success') {
                if (foamtree === null) {
                    foamtree = new CarrotSearchFoamTree({
                    id: "visualization",
                    dataObject: data,
                    groupColorDecorator: function (opts, params, vars) {
                        if (params.group.color !== undefined) {
                            vars.groupColor = params.group.color;
                            vars.labelColor = "auto";
                        }
                    },
                    onGroupHover: onGroupHover
                    });
                } else {
                    foamtree.set('dataObject', data);
                }
            }
        });
    }

    function startWorker() {
        if (typeof(Worker) !== "undefined") {
          if (worker === null) {
            worker = new Worker('worker.js');
            worker.postMessage(refreshInterval);
          }
          worker.onmessage = function(event) {
            getResources(resourceType.toLowerCase());
          };
        } else {
          console.log("Sorry! No Web Worker support.");
        }
      }
      
    function stopWorker() {
        worker.terminate();
        worker = null;
    }

    function getContexts() {
        const url = `${baseUrl}/contexts`;
        $.get(url, function(data, status) {
            if(status === 'success') {
                if(context !== null) {
                    for(let item of data) {
                        if(item.context === context) {
                            item.active = true;
                        } else {
                            item.active = false;
                        }
                    }
                }

                var $dropdown = $("#contexts");
                $.each(data, function() {
                    if(this.active === false) {
                        $dropdown.append($("<option />").val(this.context).text(this.context));
                    } else {
                        $dropdown.append($("<option />").attr('selected', 'selected').val(this.context).text(this.context));
                    }
                });
            }
        });
    }

    $('#contexts').on('change', function() {
        context = this.value;
        sessionStorage.setItem('context', context);
        getResources(resourceType.toLowerCase());
    });

    $('#sidebarCollapse').on('click', function () {
        $('#sidebar').toggleClass('active');
        $(this).toggleClass('active');
    });

    $('#refreshRange').on('change', function(){
        refreshInterval = this.value;
        $('#refreshRangeValue').text(`${refreshInterval} [s]`);
        if(worker !== undefined) {
            stopWorker();
            startWorker();
        }
    });

    $('#memoryUnit').on('change', function(){
        memoryUnit = this.value;
    });

    $('.resources').on('click', function() {
        resourceType = this.innerText;
        getResources(resourceType.toLowerCase());
    });

    getContexts();
    getResources(resourceType.toLowerCase());
    startWorker();
});