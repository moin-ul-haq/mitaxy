// Mitaxy — small UI helpers (no framework)
(function () {
  // Dismiss flash messages
  document.addEventListener("click", function (e) {
    if (e.target.classList.contains("flash__close")) {
      var flash = e.target.closest(".flash");
      if (flash) flash.remove();
    }
  });

  // Auto-hide flashes after a while
  setTimeout(function () {
    document.querySelectorAll(".flash--success").forEach(function (f) {
      f.style.transition = "opacity .4s";
      f.style.opacity = "0";
      setTimeout(function () { f.remove(); }, 400);
    });
  }, 6000);

  // Schedule form: prevent picking a past datetime
  var dt = document.querySelector('input[type="datetime-local"]');
  if (dt && !dt.min) {
    var now = new Date(Date.now() + 60000 - new Date().getTimezoneOffset() * 60000);
    dt.min = now.toISOString().slice(0, 16);
  }

  // Dashboard: live-refresh statuses while any meeting is in flight
  var board = document.querySelector("[data-statuses-url]");
  if (board && board.dataset.hasActive === "true") {
    var url = board.dataset.statusesUrl;
    var poll = setInterval(function () {
      fetch(url, { headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var stillActive = false;
          Object.keys(data.meetings).forEach(function (id) {
            var info = data.meetings[id];
            var badge = document.querySelector('[data-meeting-badge="' + id + '"]');
            if (badge && badge.textContent.trim() !== info.label) {
              badge.textContent = info.label;
              badge.className = "badge " + info.badge;
            }
            if (!info.terminal) stillActive = true;
          });
          if (!stillActive) { clearInterval(poll); location.reload(); }
        })
        .catch(function () {});
    }, 15000);
  }
})();
