const searchInput = document.getElementById('search');
const suggestionsList = document.getElementById('suggestions');
const clipMeta = document.getElementById('clip-meta');
const previewImage = document.getElementById('preview-image');
let player = null; // will be Video.js player
const exportLink = document.getElementById('export-link');
const loadingIndicator = document.getElementById('loading-indicator');
const exportFormatInputs = Array.from(document.querySelectorAll('input[name="export-format"]'));
let selectedExportFormat = localStorage.getItem('preferredExportFormat') || 'gif';

// Initialize Video.js player when available
try {
  if (window.videojs) {
      player = videojs('player', {
      controls: false,
      loop: false,
    });
  }
} catch (e) {
  // fallback: leave player null; direct element methods won't be used
  console.warn('Video.js failed to initialize', e);
}

let selectedClip = null;

initializeExportFormat();
fetchShowList();

searchInput.addEventListener('input', async () => {
  const query = searchInput.value.trim();
  if (!query) {
    suggestionsList.innerHTML = '';
    return;
  }

  const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
  const payload = await response.json();
  renderSuggestions(payload.results || []);
});

async function renderSuggestions(results) {
  suggestionsList.innerHTML = '';
  if (!results.length) {
    const empty = document.createElement('li');
    empty.textContent = 'No matches yet';
    suggestionsList.appendChild(empty);
    return;
  }

  for (const result of results) {
    const item = document.createElement('li');
    item.className = 'suggestion';
    item.innerHTML = `<strong>${escapeHtml(result.title)}</strong><br /><small>${escapeHtml(result.description || '')}</small>`;
    item.addEventListener('click', () => selectClip(result));
    suggestionsList.appendChild(item);
  }
}

async function selectClip(clip) {
  selectedClip = clip;
  suggestionsList.innerHTML = '';
  const response = await fetch(`/api/clip?id=${encodeURIComponent(clip.id)}`);
  const payload = await response.json();

  clipMeta.innerHTML = `
    <h2>${escapeHtml(payload.title)}</h2>
    <p>${escapeHtml(payload.description)}</p>
  `;

  setLoading(true, `Preparing ${selectedExportFormat.toUpperCase()} clip… (could take up to 10 seconds)`);
  hidePlayer();
  hidePreview();
  exportLink.textContent = '';
  const expResp = await fetch(`/api/export?id=${encodeURIComponent(clip.id)}&format=${selectedExportFormat}`);
  const expPayload = await expResp.json();
  setLoading(false);
  if (!expPayload || !expPayload.url) {
    exportLink.textContent = expPayload.error || 'Export failed';
    return;
  }

  const mediaUrl = expPayload.url;
  exportLink.innerHTML = `<a href="${mediaUrl}" target="_blank" rel="noreferrer">Download ${selectedExportFormat.toUpperCase()}</a>`;
  if (selectedExportFormat === 'gif') {
    if (player && player.pause) {
      try {
        player.pause();
        if (player.hide) {
          player.hide();
        }
        if (player.controls) {
          player.controls(false);
        }
      } catch (_) {}
    }
    if (previewImage) {
      previewImage.src = mediaUrl;
      previewImage.classList.remove('hidden');
    }
    if (player && player.hide) {
      player.hide();
    } else {
      const videoEl = document.getElementById('player');
      if (videoEl) {
        videoEl.classList.add('hidden');
      }
    }
    return;
  }

  if (previewImage) {
    previewImage.classList.add('hidden');
  }
  if (player && player.show) {
    player.show();
    if (player.controls) {
      player.controls(true);
    }
  } else {
    const videoEl = document.getElementById('player');
    if (videoEl) {
      videoEl.classList.remove('hidden');
      videoEl.controls = true;
    }
  }

  // Cancel any previous timeupdate handler so selections don't conflict.
  if (player && player.off) {
    // video.js player
    try { player.off('timeupdate'); } catch (e) {}
  } else if (player && player.ontimeupdate !== undefined) {
    player.ontimeupdate = null;
  }

  // Pause and set new source to the pre-rendered WebM clip
  if (player && player.src) {
    // video.js player API
    try {
      player.pause();
      player.src({ src: mediaUrl, type: 'video/mp4' });
      player.load();
      player.play().catch(() => {});
    } catch (e) {
      // fallback to element
      const el = document.getElementById('player');
      if (el) {
        el.pause();
        el.src = mediaUrl;
        el.load();
        el.play().catch(() => {});
      }
    }
  } else {
    const el = document.getElementById('player');
    if (el) {
      el.pause();
      el.src = mediaUrl;
      el.load();
      el.play().catch(() => {});
    }
  }
}

function showPlayer() {
  const videoEl = document.getElementById('player');
  if (player && player.show) {
    player.show();
  }
  if (videoEl) {
    videoEl.classList.remove('hidden');
  }
}

function hidePlayer() {
  const videoEl = document.getElementById('player');
  if (player && player.hide) {
    player.hide();
  }
  if (videoEl) {
    videoEl.classList.add('hidden');
  }
}

function showPreview() {
  if (previewImage) {
    previewImage.classList.remove('hidden');
  }
}

function hidePreview() {
  if (previewImage) {
    previewImage.src = '';
    previewImage.classList.add('hidden');
  }
}

function setLoading(isLoading, message = 'Generating clip…') {
  if (!loadingIndicator) return;
  if (isLoading) {
    hidePlayer();
    hidePreview();
    exportLink.textContent = '';
    loadingIndicator.classList.remove('hidden');
    loadingIndicator.innerHTML = `<span class="spinner" aria-hidden="true"></span> ${message}`;
    exportFormatInputs.forEach((input) => (input.disabled = true));
  } else {
    loadingIndicator.classList.add('hidden');
    exportFormatInputs.forEach((input) => (input.disabled = false));
  }
}


async function exportClip(format) {
  setLoading(true, `Generating ${format.toUpperCase()} export…`);
  exportLink.textContent = '';
  const response = await fetch(`/api/export?id=${encodeURIComponent(selectedClip.id)}&format=${format}`);
  const payload = await response.json();
  setLoading(false);
  if (payload.url) {
    exportLink.innerHTML = `<a href="${payload.url}" target="_blank" rel="noreferrer">Download ${format.toUpperCase()}</a>`;
  } else {
    exportLink.textContent = payload.error || 'Export failed';
  }
}

function getPreferredExportFormat() {
  return selectedExportFormat;
}

function setPreferredExportFormat(format) {
  selectedExportFormat = format;
  localStorage.setItem('preferredExportFormat', format);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function initializeExportFormat() {
  const preferred = selectedExportFormat;
  exportFormatInputs.forEach((input) => {
    input.checked = input.value === preferred;
    input.addEventListener('change', async () => {
      if (input.checked) {
        setPreferredExportFormat(input.value);
        if (input.value !== 'mp4' && player && player.pause) {
          try {
            player.pause();
          } catch (_) {}
        }
        if (selectedClip) {
          await selectClip(selectedClip);
        }
      }
    });
  });
}

async function fetchShowList() {
  const container = document.getElementById('indexed-shows');
  if (!container) return;
  const response = await fetch('/api/shows');
  if (!response.ok) {
    container.textContent = 'Unable to load shows.';
    return;
  }
  const payload = await response.json();
  const shows = payload.shows || [];
  if (!shows.length) {
    container.textContent = 'No shows indexed yet.';
    return;
  }
  const list = document.createElement('ul');
  list.className = 'show-list';
  for (const show of shows) {
    const item = document.createElement('li');
    item.textContent = show;
    list.appendChild(item);
  }
  container.innerHTML = '';
  container.appendChild(list);
}

