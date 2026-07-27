// Mitaxy — UI behaviors (vanilla JS, no framework)
(function () {
  "use strict";

  // ---------------------------------------------------------------- helpers
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  // ------------------------------------------------- marketing nav (burger)
  var nav = $(".nav");
  var navToggle = $("#navToggle");
  if (nav && navToggle) {
    var setOpen = function (open) {
      nav.classList.toggle("is-open", open);
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    };
    navToggle.addEventListener("click", function () {
      setOpen(!nav.classList.contains("is-open"));
    });
    $all("#navMenu a, #navMenu button", nav).forEach(function (el) {
      el.addEventListener("click", function () { setOpen(false); });
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
    document.addEventListener("click", function (e) {
      if (nav.classList.contains("is-open") && !nav.contains(e.target)) setOpen(false);
    });
    window.addEventListener("resize", function () { if (window.innerWidth > 640) setOpen(false); });
  }

  // ------------------------------------------------- app sidebar (drawer)
  var appShell = $("#appShell");
  var sideToggle = $("#sideToggle");
  var sideBackdrop = $("#sideBackdrop");
  if (appShell && sideToggle) {
    sideToggle.addEventListener("click", function () { appShell.classList.add("nav-open"); });
    if (sideBackdrop) sideBackdrop.addEventListener("click", function () { appShell.classList.remove("nav-open"); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") appShell.classList.remove("nav-open");
    });
    $all(".side a").forEach(function (a) {
      a.addEventListener("click", function () { appShell.classList.remove("nav-open"); });
    });
  }

  // ------------------------------------------------------------- flashes
  document.addEventListener("click", function (e) {
    if (e.target.classList && e.target.classList.contains("flash__close")) {
      var flash = e.target.closest(".flash");
      if (flash) flash.remove();
    }
  });
  setTimeout(function () {
    $all(".flash--success, .flash--info").forEach(function (f) {
      f.style.transition = "opacity .4s";
      f.style.opacity = "0";
      setTimeout(function () { f.remove(); }, 400);
    });
  }, 6000);

  // ------------------------------------------------- confirm-before-submit
  $all("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  // ------------------------------------------------- double-submit guard
  // A slow backend call (dispatching a bot takes a couple of seconds) invites
  // a second click — which used to send two bots into the call. Block repeat
  // submissions and give the button a busy state. pointer-events (not
  // `disabled`) so named submit-button values still reach the server.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form._mxBusy) { e.preventDefault(); return; }
    form._mxBusy = true;
    setTimeout(function () { form._mxBusy = false; }, 10000);
    $all('button[type="submit"], input[type="submit"]', form).forEach(function (b) {
      setTimeout(function () {
        b.style.pointerEvents = "none";
        b.style.opacity = "0.65";
        if (b.dataset.busyLabel) b.textContent = b.dataset.busyLabel;
        setTimeout(function () {
          b.style.pointerEvents = "";
          b.style.opacity = "";
        }, 10000);
      }, 0);
    });
  }, true);

  // ------------------------------------------------------------- modals
  $all("[data-modal-open]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var modal = document.getElementById(btn.getAttribute("data-modal-open"));
      if (modal) modal.classList.add("is-open");
    });
  });
  $all(".modal").forEach(function (modal) {
    $all("[data-modal-close]", modal).forEach(function (el) {
      el.addEventListener("click", function () { modal.classList.remove("is-open"); });
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") $all(".modal.is-open").forEach(function (m) {
      if (m.id !== "tourModal") m.classList.remove("is-open");
    });
  });

  // ------------------------------------------------------- copy to clipboard
  $all("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = $(btn.getAttribute("data-copy"));
      if (!input) return;
      input.select();
      input.setSelectionRange(0, 99999);
      var done = function () {
        var old = btn.innerHTML;
        btn.innerHTML = "Copied!";
        setTimeout(function () { btn.innerHTML = old; }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(input.value).then(done, done);
      } else {
        try { document.execCommand("copy"); } catch (err) {}
        done();
      }
    });
  });

  // ------------------------------------------- schedule form: now / later
  var seg = $("#startModeSeg");
  if (seg) {
    var dtField = $("#scheduledAtField");
    var submitBtn = $("#scheduleSubmit");
    var dtInput = dtField ? $('input[type="datetime-local"]', dtField) : null;
    var sync = function () {
      var now = $("#modeNow").checked;
      if (dtField) dtField.style.display = now ? "none" : "";
      if (submitBtn) submitBtn.textContent = submitBtn.getAttribute(now ? "data-label-now" : "data-label-later");
      if (dtInput) dtInput.required = !now;
    };
    $all("input[name=start_mode]", seg).forEach(function (r) { r.addEventListener("change", sync); });
    sync();
    // prevent picking a past datetime
    if (dtInput && !dtInput.min) {
      var m = new Date(Date.now() + 60000 - new Date().getTimezoneOffset() * 60000);
      dtInput.min = m.toISOString().slice(0, 16);
    }
  }

  // ------------------------------------------- share modal: mode switching
  var shareForm = $("#shareForm");
  if (shareForm) {
    var emailsField = $("#shareEmailsField");
    var inviteBtn = $("#shareInviteBtn");
    var syncShare = function () {
      var checked = $('input[name="visibility"]:checked', shareForm);
      var restricted = checked && checked.value === "restricted";
      if (emailsField) emailsField.style.display = restricted ? "" : "none";
      if (inviteBtn) inviteBtn.style.display = restricted ? "" : "none";
      $all(".radio-card", shareForm).forEach(function (card) {
        var input = $("input", card);
        card.classList.toggle("is-checked", input && input.checked);
      });
    };
    $all('input[name="visibility"]', shareForm).forEach(function (r) {
      r.addEventListener("change", syncShare);
    });
    syncShare();
  }

  // ------------------------------------------------------ onboarding tour
  var tour = $("#tourModal");
  if (tour) {
    var steps = $all(".tour__step", tour);
    var dots = $all(".tour__dot", tour);
    var nextBtn = $("#tourNext");
    var doneForm = $("#tourDoneForm");
    var idx = 0;
    var show = function (i) {
      idx = i;
      steps.forEach(function (s, n) { s.classList.toggle("is-on", n === i); });
      dots.forEach(function (d, n) { d.classList.toggle("is-on", n === i); });
      if (nextBtn) nextBtn.textContent = (i === steps.length - 1) ? "Let's go" : "Next";
    };
    if (nextBtn) nextBtn.addEventListener("click", function () {
      if (idx < steps.length - 1) { show(idx + 1); }
      else if (doneForm) { doneForm.submit(); }
    });
    show(0);
  }

  // ------------------------------- dashboard: live status refresh (poll)
  var board = $("[data-statuses-url]");
  if (board && board.dataset.hasActive === "true") {
    var url = board.dataset.statusesUrl;
    var poll = setInterval(function () {
      fetch(url, { headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var stillActive = false;
          Object.keys(data.meetings).forEach(function (id) {
            var info = data.meetings[id];
            var badge = $('[data-meeting-badge="' + id + '"]');
            if (badge && badge.textContent.trim() !== info.label) {
              badge.textContent = info.label;
              badge.className = "badge " + info.badge;
            }
            var hint = $('[data-meeting-hint="' + id + '"]');
            if (hint && info.hint && hint.textContent !== info.hint) {
              hint.textContent = info.hint;
            }
            if (!info.terminal) stillActive = true;
          });
          if (!stillActive) { clearInterval(poll); location.reload(); }
        })
        .catch(function () { /* transient network error — next tick retries */ });
    }, 12000);
  }
})();
