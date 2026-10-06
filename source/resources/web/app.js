'use strict';

const $ = id => document.getElementById(id);
const MODES = ['STANDBY', 'NOTICE', 'DISCUSSION', 'REST', 'QUESTION', 'CORRECT', 'WRONG'];
const AUDIO_MODES = [...MODES, 'REST_END'];
const TIMERS = ['question_seconds', 'feedback_seconds', 'rest_seconds', 'rest_reminder_seconds', 'voice_idle_seconds'];
const WORDS = {
  audioDiagnostics: ['收音診斷', 'Input diagnostics'],
  inputAge: ['資料更新', 'Input age'], captureLag: ['處理延遲', 'Processing lag'], captureDropped: ['略過過期音訊區塊', 'Stale audio blocks skipped'],
  chooseNetwork: ['選擇要搜尋的網段', 'Choose a subnet to search'], searchNetwork: ['搜尋此網段', 'Search this subnet'],
  settingsTitle: ['連線與收音設定', 'Connection and audio settings'],
  settingsHelp: ['先連接燈板與麥克風，再依需要調整偵測及音源。', 'Connect panels and a microphone, then adjust detection and audio as needed.'],
  settingsSections: ['設定區段', 'Settings sections'],
  remoteTitle: ['手機控制', 'Phone control'], remoteHelp: ['手機與電腦連上同一個 Wi‑Fi 後，用手機瀏覽器開啟下方網址。手機只提供模式按鈕；收音與音量偵測仍由電腦負責。', 'Connect phone and computer to the same Wi-Fi, then open a URL below in your phone browser. The phone only has mode buttons; the computer handles audio and noise detection.'],
  remoteOn: ['顯示手機網址', 'Show phone URL'], remoteOff: ['關閉手機連線', 'Close phone connection'],
  remoteActivate: ['啟動手機控制', 'Activate phone control'], remotePaused: ['手機已連線；控制已暫停', 'Phone connected; control paused'],
  remoteDisabled: ['未啟用', 'Disabled'], remoteWaiting: ['等待手機連線；四情境仍可用電腦語音切換', 'Waiting for phone; desktop voice can still switch scenarios'],
  remoteConnected: ['手機已連線；四情境由按鈕控制，三個互動仍可用電腦語音', 'Phone connected; scenario buttons take over, while desktop voice still handles interactions'],
  remoteCopy: ['複製網址', 'Copy URL'], remoteCopied: ['已複製', 'Copied'],
  boardImmediate: ['加入、同步與移除立即生效；燈板方向需儲存。', 'Adding, syncing and removing panels apply immediately. Save orientation separately.'],
  inputImmediate: ['切換麥克風與校準立即生效。下方偵測及語音指令修改需儲存。', 'Microphone selection and calibration apply immediately. Save detection and voice-command edits below.'],
  audioImmediate: ['音量與淡入淡出需儲存。選擇、清除音檔與隨音樂波動立即生效。', 'Save volume and fade changes. Files and music-responsive switches apply immediately.'],
  thresholdHelp: ['門檻為高於安靜校準基準的音量差（dB），數字越大越不敏感。', 'Thresholds are dB above the calibrated quiet level. Higher values are less sensitive.'],
  unsaved: ['尚未儲存的修改', 'Unsaved changes'], reset: ['捨棄修改', 'Discard changes'], saving: ['儲存中…', 'Saving…'],
  noiseUnavailable: ['請先選擇請注意或討論模式，並確認麥克風可用。', 'Choose Attention or Discussion and connect an available microphone.'],
  noNetworks: ['沒有可搜尋的網段；可直接輸入 IP。', 'No subnet available; enter a panel IP instead.'],
  allowedRange: ['範圍', 'Range'],
  brand: ['responsive classroom', 'responsive classroom'], navigation: ['主要導覽', 'Main navigation'],
  classroom: ['課堂', 'Classroom'], connection: ['連線與收音', 'Connection'], timers: ['計時', 'Timers'],
  STANDBY: ['待機', 'Standby'], NOTICE: ['請注意', 'Attention'], DISCUSSION: ['討論', 'Discussion'], REST: ['休息', 'Rest'],
  QUESTION: ['提問', 'Question'], CORRECT: ['答對', 'Correct'], WRONG: ['答錯', 'Wrong'],
  REST_END: ['休息收尾', 'Rest reminder'], scenarioAudio: ['音樂', 'MP3'], scenarioVoice: ['語音', 'Mic'],
  scenarioOn: ['開', 'On'], scenarioOff: ['關', 'Off'], remoteVoiceManaged: ['手機已接管四情境切換', 'Phone controls scenario switching'],
  voiceMasterOff: ['請先開啟左側語音總開關', 'Enable the main voice switch first'],
  currentMode: ['目前模式', 'Current mode'], manual: ['手動控制', 'Manual control'],
  manualHelp: ['點選模式，立即調整課堂信號。', 'Choose a mode to change the classroom signal.'],
  standbyHelp: ['等待下一個課堂活動', 'Ready for the next activity'], noticeHelp: ['安靜聽講', 'Listen to the teacher'],
  discussionHelp: ['允許交談，音量範圍更寬', 'More room for conversation'], restHelp: ['倒數後提醒回到課堂', 'A timed break with a reminder'],
  voiceOn: ['開啟語音', 'Enable voice'], voiceOff: ['關閉語音', 'Disable voice'],
  noiseOn: ['啟動音量偵測', 'Enable noise'], noiseOff: ['暫停音量偵測', 'Pause noise'],
  matrix: ['即時 8×8 燈板畫面', 'Live 8×8 panel frame'], audioInput: ['收音輸入', 'Microphone'],
  liveLevel: ['即時音量', 'Live input level'], heard: ['最新辨識', 'Last heard'],
  noSpeech: ['尚無語音輸入', 'No speech yet'], listening: ['正在收音', 'Listening'],
  boards: ['燈板', 'Light panels'],
  boardIp: ['燈板 IP', 'Panel IP address'], connectBoard: ['加入燈板', 'Add panel'], findBoards: ['搜尋燈板', 'Find panels'],
  localNetwork: ['加入燈板', 'Add panels'], network: ['搜尋網段', 'Search subnet'], search: ['自動搜尋', 'Auto search'],
  searching: ['搜尋中…', 'Searching…'], connecting: ['連線中…', 'Connecting…'], add: ['加入', 'Add'],
  enabled: ['同步', 'Sync'], board: ['燈板', 'Panel'], state: ['狀態', 'Status'], remove: ['移除', 'Remove'],
  selectPanel: ['選取燈板', 'Select panel'], addSelected: ['加入所選燈板', 'Add selected panels'],
  boardEmpty: ['尚未加入燈板。輸入 IP 建立連線。', 'No panels added. Enter an IP address to connect.'],
  connected: ['已確認接收', 'Receiving confirmed'], sending: ['送出中，待確認', 'Sending; awaiting receipt'],
  checking: ['確認連線中', 'Checking connection'], paused: ['已暫停', 'Paused'], disabled: ['未同步', 'Not syncing'],
  idle: ['尚未連線', 'Not connected'], conflict: ['控制衝突', 'Control conflict'], error: ['連線異常', 'Connection error'],
  orientation: ['燈板方向', 'Panel orientation'], rotation: ['旋轉', 'Rotation'], serpentine: ['蛇形排列', 'Serpentine'],
  mirrorX: ['左右鏡像', 'Mirror X'], mirrorY: ['上下鏡像', 'Mirror Y'], save: ['儲存', 'Save'],
  microphoneNoise: ['收音與偵測', 'Input and detection'], microphone: ['麥克風', 'Microphone'],
  defaultMic: ['系統預設麥克風', 'System default microphone'], micReady: ['麥克風正常', 'Microphone ready'],
  micPreparing: ['準備麥克風', 'Preparing microphone'], micError: ['麥克風異常', 'Microphone error'],
  refreshMicrophones: ['重新整理麥克風', 'Refresh microphones'], calibrate: ['安靜校準 12 秒', 'Calibrate · 12 s'],
  calibrationProgress: ['校準進度', 'Calibration progress'], calibrating: ['校準中…', 'Calibrating…'],
  risingThreshold: ['漸活躍門檻', 'Activity threshold'], loudThreshold: ['活躍門檻', 'Higher activity threshold'],
  noiseAdvanced: ['恢復門檻與持續時間', 'Recovery thresholds and timing'],
  risingDuration: ['漸活躍持續時間', 'Activity hold'], loudDuration: ['活躍持續時間', 'Higher activity hold'],
  recoveryDuration: ['恢復持續時間', 'Recovery hold'], smoothing: ['音量平滑', 'Level smoothing'],
  seconds: ['秒', 's'], quietReturn: ['恢復平穩', 'Return to steady'], loudReturn: ['降低活躍', 'Ease activity'],
  focusQuietReturn: ['請注意恢復安靜門檻', 'Attention quiet recovery threshold'],
  discussionQuietReturn: ['討論恢復安靜門檻', 'Discussion quiet recovery threshold'],
  focusLoudReturn: ['請注意離開吵鬧門檻', 'Attention loud recovery threshold'],
  discussionLoudReturn: ['討論離開吵鬧門檻', 'Discussion loud recovery threshold'],
  saveSettings: ['儲存設定', 'Save settings'], saveAudio: ['儲存音源設定', 'Save audio settings'],
  voiceCommands: ['自訂語音指令', 'Voice commands'],
  voiceDetail: ['觸發詞與辨識修正', 'Trigger words and corrections'],
  voiceHelp: ['直接說短指令，也能從整句中辨識完整觸發詞；多個別名用逗號分隔。儲存時會與收音設定一起儲存。', 'Say a short command or a complete trigger word within a sentence. Separate aliases with commas. Saving also saves input settings.'],
  corrections: ['辨識修正：誤辨文字＝觸發詞', 'Corrections: misheard text = trigger word'],
  customAudio: ['自訂音源', 'Custom audio'], volume: ['音量', 'Volume'], audioFade: ['淡入淡出', 'Audio fade'],
  stopAudio: ['停止播放', 'Stop audio'], chooseAudio: ['選擇音檔', 'Choose file'], previewAudio: ['試聽', 'Preview'],
  clearAudio: ['清除音檔', 'Clear file'], noAudio: ['未設定音檔', 'No audio file'],
  musicReactive: ['隨音樂波動', 'Music-responsive light'],
  playbackSettings: ['播放設定', 'Playback settings'],
  classTimers: ['課堂計時', 'Classroom timers'], timerHelp: ['設定下一次啟動模式時使用的時長。', 'Set durations for the next time a mode starts.'],
  question_seconds: ['提問', 'Question'], feedback_seconds: ['答對／答錯', 'Correct / Wrong'], rest_seconds: ['休息', 'Rest'],
  rest_reminder_seconds: ['休息收尾提醒', 'Rest reminder'], voice_idle_seconds: ['語音閒置回待機', 'Voice idle timeout'],
  saveTimers: ['儲存計時', 'Save timers'], idleHelp: ['閒置回待機設為 0 可停用；請注意與討論不受閒置計時影響。', 'Use 0 to disable idle timeout. Attention and Discussion remain active.'],
  saved: ['已儲存', 'Saved'], notReady: ['介面正在準備，請稍候。', 'The interface is preparing. Please wait.'],
  invalidNumber: ['請輸入範圍內的有效數字。', 'Enter a valid number within the allowed range.'],
  invalidCorrection: ['每行請使用「誤辨文字＝觸發詞」。', 'Use “misheard text = trigger word” on each line.'],
  boardStatusConnected: ['燈板已連線', 'Panel connected'], boardStatusConnecting: ['燈板連線中', 'Connecting panel'],
  boardStatusPaused: ['燈板已暫停', 'Panel paused'], boardStatusError: ['燈板連線異常', 'Panel error'],
  voiceReady: ['語音已開啟', 'Voice enabled'], voiceDisabled: ['語音已關閉', 'Voice disabled'], voicePreparing: ['語音準備中', 'Preparing voice'],
  voicePaused: ['語音指令已暫停', 'Voice commands paused'], voiceWakeOnly: ['仍可說「啟動語音」或「sensor on」', 'Say “sensor on” to resume'],
  unknownNoise: ['等待有效音量', 'Waiting for valid input'], QUIET: ['平穩', 'Steady'], RISING: ['漸活躍', 'Growing activity'], LOUD: ['活躍', 'Active'],
  warm: ['暖金柔光漫遊 · 待機', 'Golden drifting glow · Standby'], green: ['暖綠呼吸 · 平穩', 'Warm green breath · Steady'],
  orange: ['橘色橫流 · 漸活躍', 'Orange ribbon · Growing activity'], red: ['珊瑚直波 · 活躍', 'Coral wave · Active'],
  discussion_quiet: ['蜂蜜雙星接力 · 交流', 'Honey relay lights'],
  discussion_rising: ['青綠旋轉風車 · 漸活躍', 'Teal pinwheel'],
  discussion_loud: ['玫瑰環流 · 活躍', 'Rose orbit'],
  question: ['紫色光環旋轉 · 提問', 'Violet rotating arc'], correct: ['翡翠綠圓圈呼吸 · 答對', 'Emerald breathing circle'],
  wrong: ['桃紅叉號掃亮 · 答錯', 'Magenta sweeping X'], rest: ['靛藍流動波 · 休息', 'Indigo wave · Rest'],
  rest_end: ['暖白沙漏 · 收尾提醒', 'Warm white hourglass · Reminder']
};

let backend, state, language = 'zh_TW', hydratedRevision = -1;
const dirty = new Set();
const boardRows = new Map();
const draftVersions = new Map(), pending = new Map(), orientationDrafts = new Map();
const GROUPS = {'noise-form':['noise-form','voice-form'], 'voice-form':['noise-form','voice-form'], 'timers-form':['timers-form'], audio:['audio'], 'orientation-form':['orientation-form']};
function markDirty(key) {
  if (!GROUPS[key]) return;
  dirty.add(key); draftVersions.set(key, (draftVersions.get(key) || 0) + 1); renderDrafts();
}
function renderDrafts() {
  document.querySelectorAll('[data-draft-status]').forEach(e => {
    const changed = GROUPS[e.dataset.draftStatus].some(k => dirty.has(k));
    e.hidden = !changed; e.querySelector('span').textContent = text('unsaved');
  });
}
function clearDraft(keys, versions) {
  keys.forEach(k => { if (!versions || versions.get(k) === draftVersions.get(k)) dirty.delete(k); });
  renderDrafts();
}
async function saveDraft(key, button, action, payload, messageId) {
  if (button.dataset.saving) return false;
  const versions = draftVersionsFor(key);
  button.dataset.saving = 'true'; button.disabled = true; button.setAttribute('aria-busy','true');
  const original = button.textContent; button.textContent = text('saving');
  try {
    if (!await command(action, payload, messageId)) return false;
    clearDraft(GROUPS[key], versions); message(messageId,text('saved'));
    if (key === 'orientation-form' && versions.get(key) === draftVersions.get(key)) orientationDrafts.delete(payload.mac);
    return true;
  } finally { delete button.dataset.saving; button.disabled = false; button.removeAttribute('aria-busy'); button.textContent=original; }
}
function draftVersionsFor(key) { return new Map(GROUPS[key].map(k => [k,draftVersions.get(k)])); }
function makeDraftStatus(key, target) {
  const bar = document.createElement('div'); bar.className = 'draft-status'; bar.dataset.draftStatus = key; bar.hidden = true;
  const status = document.createElement('span'); status.setAttribute('role','status'); bar.append(status);
  const reset = translated('button','reset','button quiet small'); reset.type = 'button';
  reset.onclick = () => {
    clearDraft(GROUPS[key]);
    if (key === 'orientation-form') { orientationDrafts.delete($('orientation-board').value); orientationValue(); }
    else if (state) hydrateSettings(state.settings);
  };
  const actions=document.createElement('div'); actions.className='draft-actions'; actions.append(reset);
  const save=translated('button','save','button primary small');save.type='button';
  save.onclick=()=>{
    if(key==='audio')$('save-audio').click();
    else if(key==='noise-form')$('save-noise').click();
    else $(key).requestSubmit();
  };
  actions.append(save);bar.append(actions); target.append(bar);
}
let frame = new Uint8Array(192), frameChanges = 0, paintScheduled = false;
const text = key => (WORDS[key] || [key, key])[language === 'en_US' ? 1 : 0];
const setText = (id, value) => { const e = $(id); if (e.textContent !== String(value ?? '')) e.textContent = value ?? ''; };
const icon = name => { const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('class', 'icon'); const use = document.createElementNS(svg.namespaceURI, 'use'); use.setAttribute('href', `#i-${name}`); svg.append(use); return svg; };
const translated = (tag, key, className) => { const e = document.createElement(tag); if (className) e.className = className; e.dataset.t = key; e.textContent = text(key); return e; };
const message = (id, value, error = false) => { setText(id, value); $(id).classList.toggle('error', error); };

function localMessage(value) {
  if (language !== 'en_US' || !value) return value || '';
  const common = {'尚未啟動':'Not started', '語音已關閉':'Voice disabled', '等待語音指令':'Waiting for a voice command',
    '尚未校準':'Not calibrated', '已儲存':'Saved', '音量設定已儲存。':'Noise settings saved.', '語音設定已儲存。':'Voice settings saved.',
    '已儲存；新計時會使用更新後的時間。':'Saved. New timers use the updated durations.', '麥克風尚未啟動':'Microphone has not started',
    '已連線，正在送出燈光':'Connected; sending light frames', '已停止':'Stopped', '無指令':'No command', '未找到指令':'No command found',
    '語音指令已暫停；仍可說啟動語音':'Voice commands paused; say sensor on to resume', '語音指令已啟動':'Voice commands resumed',
    '語音指令已暫停':'Voice commands paused', '此情境的語音已關閉':'Voice is disabled for this scenario', '手機控制中':'Phone is controlling scenarios'};
  if (common[value]) return common[value];
  // Device and recognizer errors remain verbatim so their details are not lost.
  return value;
}

function translatePage() {
  document.documentElement.lang = language === 'en_US' ? 'en' : 'zh-Hant';
  document.querySelectorAll('[data-t]').forEach(e => { e.textContent = text(e.dataset.t); });
  document.querySelectorAll('[data-aria]').forEach(e => e.setAttribute('aria-label', text(e.dataset.aria)));
  document.querySelectorAll('[data-title]').forEach(e => { e.title = text(e.dataset.title); e.setAttribute('aria-label', e.title); });
  hydratedRevision = -1;
  $('language').textContent = language === 'en_US' ? '中文' : 'EN';
  $('language').setAttribute('aria-label', language === 'en_US' ? '切換繁體中文介面' : 'Switch to English');
  $('remote-urls').dataset.urls='';
  renderState(state); renderDrafts();
}

function navigate(page) {
  document.querySelectorAll('#pages>.page').forEach(e => { e.hidden = e.id !== `page-${page}`; });
  document.querySelectorAll('[data-page]').forEach(e => {
    if (e.dataset.page === page) e.setAttribute('aria-current', 'page'); else e.removeAttribute('aria-current');
  });
  requestPaint();
}

async function command(action, payload = {}, messageId = 'connection-error') {
  if (!backend) { message(messageId, text('notReady'), true); $(messageId).hidden = false; return false; }
  const key = `${action}:${JSON.stringify(payload)}`;
  if (pending.has(key)) return pending.get(key);
  const task = new Promise(resolve => backend.command(action, JSON.stringify(payload), response => {
    try {
      const result = JSON.parse(response);
      if (!result.ok) { message(messageId, localMessage(result.error), true); $(messageId).hidden = false; }
      else $('connection-error').hidden = true;
      resolve(Boolean(result.ok));
    } catch (error) { message(messageId, String(error), true); $(messageId).hidden = false; resolve(false); }
  }));
  pending.set(key, task);
  try { return await task; } finally { pending.delete(key); }
}

function revealControl(e) {
  for (let parent = e.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
  e.scrollIntoView({block:'center'}); e.focus();
}
function numeric(id) {
  const e = $(id), value = Number(e.value.trim());
  const valid = e.value.trim() !== '' && Number.isFinite(value) && value >= Number(e.dataset.min) && value <= Number(e.dataset.max) && (e.dataset.number !== 'int' || Number.isInteger(value));
  e.setCustomValidity(valid ? '' : text('invalidNumber')); e.setAttribute('aria-invalid',String(!valid));
  if (!valid) { revealControl(e); e.reportValidity(); throw new Error(text('invalidNumber')); }
  return value;
}

function makeFields() {
  MODES.forEach(mode => {
    const label = document.createElement('label'); label.className = 'field'; label.append(translated('span', mode));
    const input = document.createElement('input'); input.id = `alias-${mode}`; input.type = 'text'; input.maxLength = 400; input.autocomplete = 'off';
    label.append(input); $('alias-fields').append(label);
  });
  AUDIO_MODES.forEach(mode => {
    const row = document.createElement('div'); row.className = 'audio-file'; row.append(translated('strong', mode));
    const name = document.createElement('span'); name.id = `audio-name-${mode}`; name.className = 'file-name'; row.append(name);
    const choose = translated('button', 'chooseAudio', 'button'); choose.type = 'button'; choose.onclick = () => command('choose_audio', {mode: mode.toLowerCase()}, 'audio-message'); row.append(choose);
    ['preview', 'clear'].forEach(action => {
      const b = document.createElement('button'); b.type = 'button'; b.id = `audio-${action}-${mode}`; b.className = 'icon-button';
      b.dataset.title = action === 'preview' ? 'previewAudio' : 'clearAudio'; b.append(icon(action === 'preview' ? 'play' : 'cross'));
      b.onclick = () => command(`${action}_audio`, {mode: mode.toLowerCase()}, 'audio-message'); row.append(b);
    });
    const reactive = document.createElement('label'); reactive.className = 'music-reactive';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.id = `audio-reactive-${mode}`;
    checkbox.onchange = async () => {
      if (!await command('audio_reactive', {mode:mode.toLowerCase(), enabled:checkbox.checked}, 'audio-message')) checkbox.checked = !checkbox.checked;
    };
    reactive.append(checkbox, translated('span', 'musicReactive')); row.append(reactive);
    $('audio-files').append(row);
  });
  TIMERS.forEach(key => {
    const label = document.createElement('label'); label.className = 'field'; label.append(translated('span', key));
    const unit = document.createElement('span'); unit.className = 'input-unit';
    const input = document.createElement('input'); input.id = key; input.type = 'text'; input.inputMode = 'numeric';
    input.dataset.number = 'int'; input.dataset.min = key === 'voice_idle_seconds' ? '0' : '1'; input.dataset.max = '86400';
    unit.append(input, translated('span', 'seconds')); label.append(unit); $('timers-form').append(label);
  });
  const save = translated('button', 'saveTimers', 'button primary'); save.id = 'save-timers'; save.type = 'submit'; $('timers-form').append(save);
  makeDraftStatus('timers-form', $('timers-form').parentElement);
  makeDraftStatus('noise-form', $('noise-form'));
  makeDraftStatus('audio', $('audio-details').querySelector('.region-content'));
  makeDraftStatus('orientation-form', $('orientation-form').parentElement);
  document.querySelectorAll('[data-number]').forEach(e => {
    e.setAttribute('aria-description', `${text('allowedRange')}: ${e.dataset.min}–${e.dataset.max}`);
  });
}


const NOISE_IDS = {
  'noise-rising':'noise_rising_db', 'noise-loud':'noise_loud_db', 'rising-exit':'noise_rising_exit_db', 'loud-exit':'noise_loud_exit_db',
  'rising-seconds':'noise_rising_enter_seconds', 'loud-seconds':'noise_loud_enter_seconds', 'recovery-seconds':'noise_exit_seconds', 'smoothing-ms':'noise_smoothing_ms'
};

async function saveInputSettings(event, messageId) {
  event.preventDefault();
  try {
    const payload = Object.fromEntries(Object.entries(NOISE_IDS).map(([id, key]) => [key, numeric(id)]));
    payload.discussion_noise = {
      rising_db:numeric('discussion-rising'), loud_db:numeric('discussion-loud'),
      rising_exit_db:numeric('discussion-rising-exit'), loud_exit_db:numeric('discussion-loud-exit')
    };
    payload.aliases = Object.fromEntries(MODES.map(mode => [mode, $(`alias-${mode}`).value.split(/[,，、；;\n]+/).map(s => s.trim()).filter(Boolean)]));
    const corrections = {};
    for (const line of $('corrections').value.split('\n').filter(s => s.trim())) {
      const match = line.match(/^\s*(.+?)\s*[=＝]\s*(.+?)\s*$/);
      if (!match) { revealControl($('corrections')); message(messageId, text('invalidCorrection'), true); return; }
      corrections[match[1]] = match[2];
    }
    payload.corrections = corrections;
    await saveDraft('noise-form', $('save-noise'), 'save_input_settings', payload, messageId);
  } catch (_) {}
}

function setField(id, value) {
  const e = $(id), next = String(value ?? '');
  if (document.activeElement !== e && e.value !== next) e.value = next;
}
function hydrateSettings(settings) {
  if (!dirty.has('timers-form')) TIMERS.forEach(k => { setField(k, settings[k]); });
  if (!dirty.has('noise-form') && !dirty.has('voice-form')) {
    Object.entries(NOISE_IDS).forEach(([id, key]) => { setField(id, settings[key]); });
    ['rising_db','loud_db','rising_exit_db','loud_exit_db'].forEach((key, i) => {
      setField(['discussion-rising','discussion-loud','discussion-rising-exit','discussion-loud-exit'][i], settings.discussion_noise[key]);
    });
    MODES.forEach(mode => { setField(`alias-${mode}`, (settings.command_aliases[mode] || []).join(', ')); });
    setField('corrections', Object.entries(settings.command_corrections || {}).map(([a,b]) => `${a} = ${b}`).join('\n'));
  }
  if (!dirty.has('audio')) { setField('audio-volume', settings.audio_volume_percent); setField('audio-fade', settings.audio_fade_ms); }
  AUDIO_MODES.forEach(mode => {
    const file = settings.audio_files[mode.toLowerCase()] || '', name = file.split(/[\\/]/).pop();
    setText(`audio-name-${mode}`, name || text('noAudio')); $(`audio-name-${mode}`).title = file;
    $(`audio-preview-${mode}`).disabled = !file; $(`audio-clear-${mode}`).disabled = !file;
    $(`audio-reactive-${mode}`).checked = Boolean(settings.audio_reactive_modes?.[mode.toLowerCase()]);
  });
}

function updateOptions(select, values, selected) {
  const signature = JSON.stringify(values);
  if (select.dataset.options !== signature) {
    select.replaceChildren(...values.map(([value, label]) => { const e = document.createElement('option'); e.value = value; e.textContent = label; return e; }));
    select.dataset.options = signature;
  }
  if (selected !== undefined && document.activeElement !== select && select.value !== String(selected)) select.value = selected;
}

function orientationValue() {
  const board = state?.boards.find(b => b.mac === $('orientation-board').value);
  if (!board) return;
  $('rotation').value = String(board.rotation || 0); $('serpentine').checked = Boolean(board.serpentine);
  $('mirror-x').checked = Boolean(board.mirror_x); $('mirror-y').checked = Boolean(board.mirror_y);
}

function renderBoards(boards) {
  const macs = new Set(boards.map(b => b.mac));
  boardRows.forEach((row, mac) => { if (!macs.has(mac)) { row.remove(); boardRows.delete(mac); } });
  boards.forEach(board => {
    let row = boardRows.get(board.mac);
    if (!row) {
      row = document.createElement('tr'); const enabledCell = document.createElement('td'), cb = document.createElement('input'); cb.type = 'checkbox';
      cb.onchange = async () => { if (!await command('enable_board', {mac:board.mac, enabled:cb.checked}, 'board-message')) cb.checked = !cb.checked; };
      enabledCell.append(cb); const identity = document.createElement('td'); identity.append(document.createElement('strong'), document.createElement('small'));
      const statusCell = document.createElement('td'), status = document.createElement('div'); status.className = 'board-status';
      const dot = document.createElement('i'); dot.className = 'status-dot'; status.append(dot, document.createElement('span')); statusCell.append(status);
      const action = document.createElement('td'), remove = document.createElement('button'); remove.type='button'; remove.className = 'icon-button'; remove.append(icon('cross'));
      remove.onclick = () => command('remove_board', {mac:board.mac}, 'board-message'); action.append(remove);
      row.append(enabledCell, identity, statusCell, action); boardRows.set(board.mac, row); $('board-list').append(row);
    }
    const cells = row.children, cb = cells[0].firstChild; cb.checked = board.enabled !== false;
    cb.setAttribute('aria-label', `${text('enabled')} ${board.name || board.ip}`);
    cells[1].firstChild.textContent = board.name || 'WLED'; cells[1].lastChild.textContent = board.ip;
    cells[1].title = `${board.name || 'WLED'} · ${board.ip} · ${board.mac}`;
    const status = cells[2].firstChild; status.firstChild.className = `status-dot ${board.status === 'connected' ? 'ok' : ['connecting','sending','checking'].includes(board.status) ? 'loading' : ['error','conflict'].includes(board.status) ? 'error' : ''}`;
    status.lastChild.textContent = text(board.status); status.title = localMessage(board.health);
    cells[3].firstChild.title = text('remove'); cells[3].firstChild.setAttribute('aria-label', `${text('remove')} ${board.name || board.ip}`);
  });
  $('board-empty').hidden = boards.length > 0;
  $('board-list').closest('table').querySelector('thead').hidden = !boards.length;
  $('orientation-form').closest('details').hidden = !boards.length;
  const selected = $('orientation-board').value;
  updateOptions($('orientation-board'), boards.map(b => [b.mac, `${b.name || 'WLED'} · ${b.ip}`]), macs.has(selected) ? selected : boards[0]?.mac);
  $('orientation-form').querySelectorAll('input,select,button').forEach(e => { e.disabled = !boards.length; });
  if (!dirty.has('orientation-form')) orientationValue();
}

function patternKey(current) {
  if (current.overlay) return current.overlay.toLowerCase();
  if (current.base_mode === 'REST') return current.rest_stage === 'REMINDER' ? 'rest_end' : 'rest';
  if (!['NOTICE','DISCUSSION'].includes(current.base_mode) || !current.noise_enabled || current.noise_state === 'UNKNOWN') return 'warm';
  if (current.base_mode === 'DISCUSSION') return {QUIET:'discussion_quiet',RISING:'discussion_rising',LOUD:'discussion_loud'}[current.noise_state];
  return {QUIET:'green',RISING:'orange',LOUD:'red'}[current.noise_state];
}

function renderState(value) {
  if (!value) return; state = value; window.classroom.state = state;
  const settings = state.settings, current = state.current, statuses = state.statuses;
  if (settings.language !== language) { language = settings.language; translatePage(); return; }
  if (hydratedRevision !== state.settings_revision) { hydrateSettings(settings); hydratedRevision = state.settings_revision; }
  setText('current-mode', text(current.mode));
  setText('countdown', current.remaining_seconds === null ? '' : `${Math.ceil(current.remaining_seconds)} ${text('seconds')}`);
  setText('pattern-label', text(patternKey(current)));
  $('stop-audio-live').hidden = !statuses.audio?.playing;
  document.querySelectorAll('[data-mode]').forEach(e => { const selected = e.dataset.mode === current.mode; e.classList.toggle('selected', selected); if(e.getAttribute('aria-pressed')!==String(selected))e.setAttribute('aria-pressed', String(selected)); });
  setText('question-duration', `${settings.question_seconds} ${text('seconds')}`);
  document.querySelectorAll('.feedback-duration').forEach(e => { e.textContent = `${settings.feedback_seconds} ${text('seconds')}`; });
  [['voice-toggle',settings.voice_enabled && !statuses.speech.command_paused,'voiceOff','voiceOn'],['noise-toggle',current.noise_enabled,'noiseOff','noiseOn']].forEach(([id,on,a,b]) => {
    $(id).setAttribute('aria-pressed', String(on)); $(id).querySelector('span').textContent = text(on ? a : b);
  });
  const noiseMode = ['NOTICE','DISCUSSION'].includes(current.base_mode);
  $('noise-toggle').disabled = !noiseMode || !statuses.microphone.available;
  $('noise-toggle').title = $('noise-toggle').disabled ? text('noiseUnavailable') : '';
  const remote = state.remote || {enabled:false,connected:false,urls:[]};
  document.querySelectorAll('[data-scenario-audio],[data-scenario-voice]').forEach(button=>{
    const mode=(button.dataset.scenarioAudio||button.dataset.scenarioVoice).toLowerCase();
    const audio=Boolean(button.dataset.scenarioAudio);
    const enabled=Boolean((audio ? settings.audio_enabled_modes : settings.voice_mode_enabled)?.[mode]);
    button.setAttribute('aria-pressed',String(enabled));
    button.setAttribute('aria-label',`${text(mode.toUpperCase())} ${text(audio ? 'scenarioAudio' : 'scenarioVoice')} ${text(enabled ? 'scenarioOn' : 'scenarioOff')}`);
    button.disabled=!audio && remote.active;
    button.title=!audio && remote.active ? text('remoteVoiceManaged') : !audio && !settings.voice_enabled ? text('voiceMasterOff') : button.getAttribute('aria-label');
  });
  $('remote-toggle').setAttribute('aria-pressed',String(remote.enabled));
  $('remote-toggle').querySelector('span').textContent=text(remote.enabled ? 'remoteOff' : 'remoteOn');
  $('remote-active').disabled=!remote.enabled;
  $('remote-active').checked=Boolean(remote.armed);
  setText('remote-status',text(remote.active ? 'remoteConnected' : remote.connected ? 'remotePaused' : remote.enabled ? 'remoteWaiting' : 'remoteDisabled'));
  $('remote-urls').hidden=!remote.enabled;
  const remoteUrls=JSON.stringify(remote.urls);
  if($('remote-urls').dataset.urls!==remoteUrls){
    $('remote-urls').dataset.urls=remoteUrls;$('remote-urls').replaceChildren();
    remote.urls.forEach(url=>{
      const row=document.createElement('div'),address=document.createElement('code'),copy=document.createElement('button');
      address.textContent=url;copy.type='button';copy.className='button small';copy.textContent=text('remoteCopy');
      copy.onclick=async()=>{if(await command('copy_remote',{url},'remote-message'))copy.textContent=text('remoteCopied');};
      row.append(address,copy);$('remote-urls').append(row);
    });
  }
  $('stop-audio').disabled = !statuses.audio?.playing;
  $('stop-audio-live').hidden = !statuses.audio?.playing;
  const active = state.boards.filter(b => b.enabled !== false), online = active.filter(b => b.status === 'connected');
  const boardKey = !statuses.output.enabled ? 'boardStatusPaused' : online.length ? 'boardStatusConnected' : active.some(b=>['error','conflict'].includes(b.status)) ? 'boardStatusError' : active.length ? 'boardStatusConnecting' : 'idle';
  setText('board-title', text(boardKey)); setText('board-detail', online.length ? `${online.length}/${active.length} · ${online[0].ip}` : active[0]?.ip || 'WLED');
  $('board-title').title = active.map(b=>`${b.ip}: ${text(b.status)}`).join('\n'); $('board-dot').className = `status-dot ${online.length ? 'ok' : boardKey === 'boardStatusError' ? 'error' : active.length && statuses.output.enabled ? 'loading' : ''}`;
  setText('microphone-title', text(statuses.microphone.ok ? 'micReady' : state.busy.audio_stopping ? 'micPreparing' : 'micError'));
  const microphone = state.microphones.find(m=>m.id === settings.microphone_device_id);
  setText('microphone-detail', microphone?.id ? microphone.name : text('defaultMic'));
  $('microphone-title').title = localMessage(statuses.microphone.message); $('microphone-dot').className = `status-dot ${statuses.microphone.ok ? 'ok' : 'error'}`;
  setText('speech-title', text(!settings.voice_enabled ? 'voiceDisabled' : statuses.speech.command_paused ? 'voicePaused' : statuses.speech.ok ? 'voiceReady' : 'voicePreparing'));
  setText('speech-detail', statuses.speech.command_paused ? text('voiceWakeOnly') : localMessage(statuses.speech.message) || 'SenseVoice · Offline'); $('speech-detail').title = $('speech-detail').textContent;
  $('speech-dot').className = `status-dot ${settings.voice_enabled ? statuses.speech.ok ? 'ok' : 'loading' : ''}`;
  const n = state.noise, rawLevel = Number.isFinite(n.dbfs) ? `${n.dbfs.toFixed(1)} dBFS` : '— dBFS';
  const smoothLevel = Number.isFinite(n.smoothed_dbfs) ? `${n.smoothed_dbfs.toFixed(1)} dBFS` : rawLevel;
  setText('live-dbfs', rawLevel); setText('noise-reading', smoothLevel + (Number.isFinite(n.relative_db) ? ` · ${n.relative_db >= 0 ? '+' : ''}${n.relative_db.toFixed(1)} dB` : ''));
  $('noise-reading').title=$('noise-reading').textContent;
  setText('input-age', `${text('inputAge')}: ${Number.isFinite(n.age_ms) ? Math.round(n.age_ms)+' ms' : '—'}`);
  setText('capture-lag', `${text('captureLag')}: ${Number.isFinite(n.capture_lag_ms) ? Math.round(n.capture_lag_ms)+' ms' : '—'}`);
  setText('capture-dropped', `${text('captureDropped')}: ${n.capture_dropped || 0}`);
  $('live-meter').value = Number.isFinite(n.dbfs) ? Math.max(-100,n.dbfs) : -100;
  $('calibration-progress').value = n.calibration_progress || 0; $('calibrate').disabled = state.busy.calibration || !statuses.microphone.available;
  $('calibrate').textContent = state.busy.calibration ? text('calibrating') : text('calibrate');
  setText('transcript', state.transcript.corrected || state.transcript.raw || text(state.transcript.active ? 'listening' : 'noSpeech'));
  $('transcript').title = state.transcript.raw || ''; setText('command-result', localMessage(state.transcript.status));
  $('command-result').title = $('command-result').textContent;
  const boardSignature = JSON.stringify([language,state.boards]);
  if ($('board-list').dataset.signature !== boardSignature) { renderBoards(state.boards); $('board-list').dataset.signature = boardSignature; }
  updateOptions($('microphone'), (state.microphones.length ? state.microphones : [{id:'',name:''}]).map(m=>[m.id, m.id ? m.name : text('defaultMic')]), settings.microphone_device_id);
  const networks = state.networks.map(n=>[n.cidr, `${n.name} · ${n.cidr}`]), selectedNetwork = $('network').value;
  const networkSelection = networks.some(([cidr])=>cidr===selectedNetwork) ? selectedNetwork : networks[0]?.[0];
  updateOptions($('network'), networks.length ? networks : [['',text('noNetworks')]], networkSelection);
  $('network').disabled = !networks.length; $('network').title = !networks.length ? text('noNetworks') : '';
  if (networks.length <= 1) $('network-details').hidden = true;
  $('network-help').hidden = networks.length > 0;
  $('scan-network').disabled = state.busy.scan || !networks.length;
  $('scan-button').setAttribute('aria-expanded',String(!$('network-details').hidden));
  $('scan-button').disabled = state.busy.scan || !state.networks.length; $('scan-button').textContent = text(state.busy.scan ? 'searching' : 'search');
  $('connect-button').disabled = state.busy.connect; $('connect-button').textContent = text(state.busy.connect ? 'connecting' : 'connectBoard');
  const foundSignature = JSON.stringify([language,state.found_boards]);
  if ($('found-boards').dataset.signature !== foundSignature) {
    const selectedFound = new Set(Array.from($('found-boards').querySelectorAll('input:checked'), e=>e.value));
    const entries = state.found_boards.map(b => {
      const row=document.createElement('label'); row.className='found-panel';
      const check=document.createElement('input'); check.type='checkbox'; check.value=b.ip; check.dataset.foundPanel=''; check.checked=selectedFound.has(b.ip);
      check.setAttribute('aria-label',`${text('selectPanel')} ${b.name || 'WLED'} ${b.ip}`);
      const identity=document.createElement('span'); const name=document.createElement('strong'),ip=document.createElement('small');
      name.textContent=b.name || 'WLED'; ip.textContent=`${b.ip} · ${b.mac}`; identity.append(name,ip); row.append(check,identity); return row;
    });
    if(entries.length){
      const add=translated('button','addSelected','button primary'); add.id='add-selected-boards'; add.type='button'; add.disabled=!entries.some(row=>row.querySelector('input').checked);
      entries.forEach(row=>{row.querySelector('input').onchange=()=>{add.disabled=!$('found-boards').querySelector('input:checked');};});
      add.onclick=()=>command('add_found',{ips:Array.from($('found-boards').querySelectorAll('input:checked'),e=>e.value)},'board-message');
      entries.push(add);
    }
    $('found-boards').replaceChildren(...entries);
    $('found-boards').dataset.signature = foundSignature;
  }
  ['board','noise','voice','audio','timers'].forEach(key => { const e=$(`${key}-message`), value=state.messages[key]; if (value && e.dataset.serverMessage !== `${language}:${value}`) { e.dataset.serverMessage=`${language}:${value}`; message(e.id, localMessage(value)); } });
}

function requestPaint() {
  if (paintScheduled) return;
  paintScheduled = true;
  requestAnimationFrame(() => { paintScheduled=false; paintMatrix(); });
}

function paintMatrix() {
    if($('page-classroom').hidden) return;
    const canvas=$('matrix'), ctx=canvas.getContext('2d',{alpha:false}), box=$('matrix-stage').getBoundingClientRect();
    const size=Math.max(1,Math.floor(Math.min(box.width,box.height))), dpr=window.devicePixelRatio || 1;
    canvas.style.width=`${size}px`; canvas.style.height=`${size}px`;
    if (canvas.width !== Math.round(size*dpr)) { canvas.width=Math.round(size*dpr); canvas.height=canvas.width; }
    ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,size,size); ctx.fillStyle='#10191d'; ctx.fillRect(0,0,size,size);
    const cell=size/9, radius=cell*.32;
    for(let i=0;i<64;i++) {
      const x=(i%8+1)*cell,y=(Math.floor(i/8)+1)*cell,r=frame[i*3],g=frame[i*3+1],b=frame[i*3+2];
      ctx.fillStyle= r||g||b ? `rgb(${r},${g},${b})` : '#263035';
      ctx.beginPath(); ctx.arc(x,y,radius,0,Math.PI*2); ctx.fill();
    }
}

function wireEvents() {
  const requireState = callback => () => {
    if (!state) { message('connection-error', text('notReady'), true); $('connection-error').hidden = false; return; }
    callback();
  };
  document.querySelectorAll('[data-page],[data-go]').forEach(e=>e.addEventListener('click',()=>navigate(e.dataset.page || e.dataset.go)));
  document.querySelectorAll('[data-mode]').forEach(e=>e.addEventListener('click',()=>command('mode',{mode:e.dataset.mode})));
  document.querySelectorAll('[data-scenario-audio]').forEach(button=>button.onclick=()=>command('scenario_audio',{mode:button.dataset.scenarioAudio,enabled:button.getAttribute('aria-pressed')!=='true'}));
  document.querySelectorAll('[data-scenario-voice]').forEach(button=>button.onclick=()=>command('scenario_voice',{mode:button.dataset.scenarioVoice,enabled:button.getAttribute('aria-pressed')!=='true'}));
  $('language').onclick=()=>command('language',{language:language==='en_US'?'zh_TW':'en_US'});
  $('voice-toggle').onclick=requireState(()=>command('voice',{enabled:!state.settings.voice_enabled || state.statuses.speech.command_paused}));
  $('noise-toggle').onclick=requireState(()=>command('detection',{enabled:!state.current.noise_enabled}));
  $('remote-toggle').onclick=requireState(()=>command('remote',{enabled:!state.remote.enabled},'remote-message'));
  $('remote-active').onchange=async event=>{if(!await command('remote_active',{enabled:event.target.checked},'remote-message'))event.target.checked=!event.target.checked;};
  $('save-noise').addEventListener('click', event => event.stopPropagation());
  $('save-audio').addEventListener('click', event => event.stopPropagation());
  $('connect-form').onsubmit=e=>{e.preventDefault();command('connect',{ip:$('board-ip').value.trim()},'board-message');};
  $('scan-button').setAttribute('aria-controls','network-details');
  $('scan-button').onclick=()=>{
    if (!state?.networks.length) return;
    if (state.networks.length === 1) { command('scan',{cidr:state.networks[0].cidr},'board-message'); return; }
    $('network-details').hidden = !$('network-details').hidden;
    $('scan-button').setAttribute('aria-expanded',String(!$('network-details').hidden));
    if (!$('network-details').hidden) $('network').focus();
  };
  $('scan-network').onclick=()=>command('scan',{cidr:$('network').value},'board-message');
  $('refresh-microphones').onclick=()=>command('refresh_microphones',{},'noise-message');
  $('microphone').onchange=()=>command('microphone',{device_id:$('microphone').value},'noise-message');
  $('calibrate').onclick=()=>command('calibrate',{},'noise-message');
  let orientationSelection = '';
  $('orientation-board').addEventListener('focus',()=>{orientationSelection=$('orientation-board').value;});
  $('orientation-board').onchange=()=>{
    if (dirty.has('orientation-form') && orientationSelection) orientationDrafts.set(orientationSelection, ['rotation','serpentine','mirror-x','mirror-y'].map(id=>$(id).type==='checkbox'?$(id).checked:$(id).value));
    dirty.delete('orientation-form'); orientationValue(); orientationSelection=$('orientation-board').value;
    const draft=orientationDrafts.get(orientationSelection);
    if (draft) { ['rotation','serpentine','mirror-x','mirror-y'].forEach((id,i)=>{if($(id).type==='checkbox')$(id).checked=draft[i];else $(id).value=draft[i];}); markDirty('orientation-form'); }
    renderDrafts();
  };
  $('orientation-form').onsubmit=async e=>{e.preventDefault();await saveDraft('orientation-form',e.target.querySelector('button[type=submit]'),'orientation',{mac:$('orientation-board').value,rotation:Number($('rotation').value),serpentine:$('serpentine').checked,mirror_x:$('mirror-x').checked,mirror_y:$('mirror-y').checked},'board-message');};
  $('timers-form').onsubmit=async e=>{e.preventDefault();try{const p=Object.fromEntries(TIMERS.map(k=>[k,numeric(k)]));await saveDraft('timers-form',$('save-timers'),'save_timers',p,'timers-message');}catch(_) {}};
  $('noise-form').onsubmit=e=>saveInputSettings(e,'noise-message');
  $('voice-form').onsubmit=e=>saveInputSettings(e,'voice-message');
  $('save-audio').onclick=async()=>{try{await saveDraft('audio',$('save-audio'),'audio_settings',{volume_percent:numeric('audio-volume'),fade_ms:numeric('audio-fade')},'audio-message');}catch(_) {}};
  ['stop-audio','stop-audio-live'].forEach(id=>{$(id).onclick=()=>command('stop_audio',{},'audio-message');});
  document.addEventListener('input',e=>{e.target.setCustomValidity?.(''); e.target.removeAttribute('aria-invalid');const form=e.target.closest('form');if(form)markDirty(form.id);if(['audio-volume','audio-fade'].includes(e.target.id))markDirty('audio');});
  document.addEventListener('change',e=>{const form=e.target.closest('form');if(form && e.target.id!=='orientation-board')markDirty(form.id);});
  // Numeric controls are text inputs. Selects also reject wheel changes, focused or not.
  document.addEventListener('wheel',e=>{if(e.target.closest('input,select'))e.preventDefault();},{capture:true,passive:false});
  document.querySelectorAll('[data-section]').forEach(button=>button.onclick=()=>{
    const section=$(button.dataset.section);
    for (let e=section;e;e=e.parentElement) if(e.tagName==='DETAILS')e.open=true;
    section.scrollIntoView({block:'start'}); section.querySelector('summary').focus({preventScroll:true});
  });
  new ResizeObserver(requestPaint).observe($('matrix-stage'));
}

window.classroom={ready:false,state:null,command,navigate,get frameChanges(){return frameChanges;}};
makeFields(); wireEvents(); translatePage(); requestPaint();
if (typeof QWebChannel !== 'undefined' && window.qt?.webChannelTransport) {
  new QWebChannel(qt.webChannelTransport, channel=>{
    backend=channel.objects.classroom;
    backend.stateChanged.connect(data=>{try{renderState(JSON.parse(data));}catch(error){message('connection-error',String(error),true);$('connection-error').hidden=false;}});
    backend.frameChanged.connect(data=>{const raw=atob(data);if(raw.length!==192)return;frame=Uint8Array.from(raw,c=>c.charCodeAt(0));frameChanges++;requestPaint();});
    backend.error.connect(value=>{message('connection-error',localMessage(value),true);$('connection-error').hidden=false;});
    backend.snapshot_json(data=>{renderState(JSON.parse(data));window.classroom.ready=true;});
  });
} else {message('connection-error',text('notReady'),true);$('connection-error').hidden=false;}
