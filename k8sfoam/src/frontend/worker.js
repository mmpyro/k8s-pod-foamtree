const second = 1000;
let interval = 60*second;

function refresh() {
    postMessage(null);
}

self.addEventListener("message", function(e) {
    interval = e.data * second;
    setInterval(refresh, interval);
}, false);

