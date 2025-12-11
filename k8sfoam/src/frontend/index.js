// Move foamtree to global scope for debugging
let foamtree = null;

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
    let resourceType = resourceTypes.cpu;
    let memoryUnit = memoryUnits.MB;
    let refreshInterval = 180;
    let worker = null;
    let context = sessionStorage.getItem('context');

    function convertMemory(memory, memoryUnit) {
        switch (memoryUnit) {
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

        if (event.group !== null && event.group !== undefined) {
            selectedFoam.label = event.group.label;
            selectedFoam.weight = event.group.weight;
        }

        if (selectedFoam.label !== null && selectedFoam.weight !== null) {
            let content = '';
            if (resourceType === resourceTypes.cpu) {
                content = `${selectedFoam.label}, ${resourceType}: ${selectedFoam.weight}`;
            } else {
                content = `${selectedFoam.label}, ${resourceType}: ${convertMemory(selectedFoam.weight, memoryUnit)}`;
            }
            $('#k8sSelectedItem').text(content);
        } else {
            $('#k8sSelectedItem').text('Hover over a group to see details');
        }
    }

    function getResources(resourceType) {
        let url;
        if (context === null) {
            url = `${baseUrl}/resources/${resourceType}`;
        } else {
            url = `${baseUrl}/resources/${resourceType}?context=${context}`;
        }
        $('#k8sTitle').text(`K8s ${resourceType.toUpperCase()} Resources`);
        $.get(url, function (data, status) {
            if (status === 'success') {
                if (foamtree === null) {
                    try {
                        console.log('Initializing FoamTree with data:', data);
                        // Make visualization div have dimensions but invisible before FoamTree init
                        // In new CSS, #visualization is always block, just verify opacity
                        $('#visualization').css({ opacity: 0 });

                        foamtree = new CarrotSearchFoamTree({
                            id: "visualization",
                            dataObject: data,
                            layout: "squarified",
                            stacking: "flattened",
                            pixelRatio: window.devicePixelRatio || 1,
                            groupColorDecorator: function (opts, params, vars) {
                                if (params.group.color !== undefined) {
                                    vars.groupColor = params.group.color;
                                    vars.labelColor = "auto";
                                }
                            },
                            onGroupHover: onGroupHover
                        });
                        // Hide loading indicator and show visualization
                        console.log('FoamTree initialized successfully, hiding loading indicator');
                        $('#loading').hide();
                        $('#visualization').animate({ opacity: 1 }, 500);

                        // Handle resizing
                        window.addEventListener("resize", function () {
                            if (foamtree) foamtree.resize();
                        });

                    } catch (error) {
                        console.error('Error initializing FoamTree:', error);
                        $('#loading').html('<div class="loading-content"><p class="loading-text" style="color: #ff6b6b;">Error loading visualization. Check console for details.</p></div>');
                    }
                } else {
                    foamtree.set('dataObject', data);
                }
            }
        });
    }

    function startWorker() {
        if (typeof (Worker) !== "undefined") {
            if (worker === null) {
                worker = new Worker('worker.js');
                worker.postMessage(refreshInterval);
            }
            worker.onmessage = function (event) {
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
        $.get(url, function (data, status) {
            if (status === 'success') {
                if (context !== null) {
                    for (let item of data) {
                        if (item.context === context) {
                            item.active = true;
                        } else {
                            item.active = false;
                        }
                    }
                }

                var $dropdown = $("#contexts");
                $.each(data, function () {
                    if (this.active === false) {
                        $dropdown.append($("<option />").val(this.context).text(this.context));
                    } else {
                        $dropdown.append($("<option />").attr('selected', 'selected').val(this.context).text(this.context));
                    }
                });
            }
        });
    }

    $('#contexts').on('change', function () {
        context = this.value;
        sessionStorage.setItem('context', context);
        getResources(resourceType.toLowerCase());
    });

    $('#sidebarCollapse').on('click', function () {
        $('#sidebar').toggleClass('active');
        $(this).toggleClass('active');

        // Wait for CSS transition to complete (300ms) then resize FoamTree
        setTimeout(function () {
            if (foamtree) foamtree.resize();
        }, 310);
    });

    $('#refreshRange').on('change', function () {
        refreshInterval = this.value;
        $('#refreshRangeValue').text(`${refreshInterval}s`);
        if (worker !== undefined) {
            stopWorker();
            startWorker();
        }
    });

    $('#memoryUnit').on('change', function () {
        memoryUnit = this.value;
    });

    $('.resources').on('click', function (e) {
        e.preventDefault(); // Prevent default anchor behavior
        // trim() to remove whitespace from icon spacing
        resourceType = this.innerText.trim();
        getResources(resourceType.toLowerCase());
    });

    $('#refreshButton').on('click', function () {
        console.log('Manual refresh triggered');
        getResources(resourceType.toLowerCase());
    });

    getContexts();
    getResources(resourceType.toLowerCase());
    startWorker();
});