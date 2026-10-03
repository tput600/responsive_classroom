"""Offline web interface inside a native, resizable desktop window."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# The UI is 2D; software rendering also avoids driver-dependent Chromium failures.
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS','--disable-gpu --disable-accelerated-2d-canvas')
from PySide6.QtCore import QCoreApplication, QUrl, Qt

if QCoreApplication.instance() is None:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow
from classroom_core import Settings, SettingsRepository
from classroom_resources import APP_NAME, VERSION, application_root, config_dir, default_rest_source, self_test_report, verify_model_assets
from classroom_service import ClassroomService


class _LocalPage(QWebEnginePage):
    def __init__(self, profile, entry, parent):
        super().__init__(profile, parent)
        self.entry = entry
        self.errors = []

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return not is_main_frame or url == self.entry

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.errors.append(f'{source}:{line}: {message}')


class ClassroomWindow(QMainWindow):
    def __init__(self, repository=None, start_io=True):
        super().__init__()
        self.setWindowTitle(f'Responsive Classroom · {VERSION}')
        self.resize(1040,720)
        self.setMinimumSize(700,520)
        self._shutdown_complete = False
        root = application_root()
        self.service = ClassroomService(repository or SettingsRepository(config_dir()/'settings.json'),
                                        root, start_io=start_io, parent=self)
        self.service.shutdownFinished.connect(self._shutdown_finished)
        self.service.fileRequested.connect(self._choose_audio)
        self.view = QWebEngineView(self)
        self.profile = QWebEngineProfile(self.view)
        self.entry = QUrl.fromLocalFile(str(root/'web/index.html'))
        self.page = _LocalPage(self.profile, self.entry, self.view)
        self.view.setPage(self.page)
        options = self.page.settings()
        for option, enabled in ((QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,False),
                                (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,False),
                                (QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,False),
                                (QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,True)):
            options.setAttribute(option,enabled)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject('classroom',self.service)
        self.page.setWebChannel(self.channel)
        self.setCentralWidget(self.view)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.load(self.entry)

    def _choose_audio(self,mode):
        path,_ = QFileDialog.getOpenFileName(self,'選擇音檔 / Choose audio','','Audio (*.wav *.mp3)')
        if path:
            self.service.import_audio(mode,path)

    def _shutdown_finished(self):
        self._shutdown_complete = True
        self.close()

    def closeEvent(self,event):
        if self._shutdown_complete:
            event.accept()
        else:
            event.ignore()
            self.setEnabled(False)
            self.service.close()


def _spin(app,condition,seconds=10):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(.01)
    raise TimeoutError('Web UI operation timed out')


def _javascript(app,window,script):
    result=[]
    window.page.runJavaScript(script,lambda value:result.append(value))
    _spin(app,lambda:bool(result))
    return result[0]


def ui_smoke_report(path,captures=False):
    """Exercise rendered HTML and its real channel with temporary settings and no device I/O."""
    app=QApplication.instance() or QApplication([])
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    report={'version':VERSION,'gui_loaded':False,'modes':{},'languages':[],
            'timer_fields':[],'layouts':[],'voice_flow':{},'errors':[]}
    with tempfile.TemporaryDirectory(prefix='classroom-web-') as temporary:
        repo=SettingsRepository(Path(temporary)/'settings.json')
        repo.save(Settings(wled_devices=[]))
        window=ClassroomWindow(repo,start_io=False)
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        window.show()
        try:
            _spin(app,lambda:bool(_javascript(app,window,'Boolean(window.classroom?.ready)')),25)
            report['gui_loaded']=True
            from PySide6.QtMultimedia import QAudioBufferOutput, QAudioOutput, QMediaFormat, QMediaPlayer
            from classroom_playback import AudioPlayback
            containers=QMediaFormat().supportedFileFormats(QMediaFormat.ConversionMode.Decode)
            report['audio_decoders']={'wav':QMediaFormat.FileFormat.Wave in containers,
                                      'mp3':QMediaFormat.FileFormat.MP3 in containers}
            report['advertised_audio_decoders']=dict(report['audio_decoders'])
            rest_file=default_rest_source()
            probe=QMediaPlayer(window)
            probe.setSource(QUrl.fromLocalFile(str(rest_file)))
            _spin(app,lambda:probe.mediaStatus() in (QMediaPlayer.MediaStatus.LoadedMedia,
                  QMediaPlayer.MediaStatus.InvalidMedia),15)
            report['rest_music']={'loaded':probe.error()==QMediaPlayer.Error.NoError and
                probe.duration()>0 and bool(probe.audioTracks()),'bytes':rest_file.stat().st_size,
                'duration_ms':probe.duration(),'audio_tracks':len(probe.audioTracks())}
            output=QAudioOutput(probe)
            output.setMuted(True)
            probe.setAudioOutput(output)
            buffer_output=QAudioBufferOutput(probe)
            probe.setAudioBufferOutput(buffer_output)
            pcm=[]
            buffer_output.audioBufferReceived.connect(lambda b:pcm.append(AudioPlayback._buffer_rms(b)))
            probe.setPosition(30_000)
            probe.play()
            _spin(app,lambda:any(v>1e-5 for v in pcm),4)
            report['rest_music']['decoded_pcm_peak']=max(pcm)
            # Some Linux FFmpeg builds omit MP3 from Qt's advertised container
            # list despite decoding it. Require actual decoded MP3 PCM instead.
            report['audio_decoders']['mp3']=(rest_file.suffix.lower()=='.mp3' and
                report['rest_music']['loaded'] and report['rest_music']['decoded_pcm_peak']>1e-5)
            probe.stop()
            probe.setSource(QUrl())
            report['page_count']=_javascript(app,window,'document.querySelectorAll("#pages>.page").length')
            report['timer_fields']=json.loads(_javascript(app,window,'JSON.stringify(Array.from(document.querySelectorAll("#timers-form input"),e=>e.id))'))
            for mode in ('STANDBY','NOTICE','DISCUSSION','REST','QUESTION','CORRECT','WRONG'):
                _javascript(app,window,f'document.querySelector("[data-mode={mode}]").click()')
                _spin(app,lambda:window.service.snapshot()['current']['mode']==mode)
                report['modes'][mode]=window.service.snapshot()['current']['mode']
            from classroom_audio import CommandParser
            from classroom_core import BaseMode,Overlay
            service=window.service
            parser=CommandParser(service.settings,cooldown_seconds=0)
            service._apply_command(parser.parse('one two three question'),service.controller.manual_revision)
            report['voice_flow']['sentence_question']=service.controller.state.overlay is Overlay.QUESTION
            service.command('mode','{"mode":"NOTICE"}')
            service._apply_command(parser.parse('ordinary conversation'),service.controller.manual_revision)
            report['voice_flow']['invalid_keeps_mode']=service.controller.state.base_mode is BaseMode.NOTICE
            revision=service.controller.manual_revision
            service.command('mode','{"mode":"DISCUSSION"}')
            service._apply_command(parser.parse('wrong'),revision)
            report['voice_flow']['manual_priority']=service.controller.state.base_mode is BaseMode.DISCUSSION and service.controller.state.overlay is None
            report['voice_flow']['continuous_has_no_idle_deadline']=service.controller.remaining_seconds('voice') is None
            service.command('mode','{"mode":"STANDBY"}')
            # Use the actual web channel to select several discovered boards and
            # save synchronization choices, without sending hardware packets.
            from classroom_hardware import WledDevice
            service._found_boards=[WledDevice(f'192.0.2.{i}',f'WLED {i}',f'00000000000{i}','test',64)
                                   for i in (1,2,3)]
            service._publish()
            _spin(app,lambda:_javascript(app,window,'document.querySelectorAll("#found-boards input").length')==3)
            _javascript(app,window,'''window.classroom.navigate('connection');
              const choices=document.querySelectorAll('#found-boards input');
              choices[0].click();choices[1].click();document.getElementById('add-selected-boards').click();''')
            _spin(app,lambda:len(service.settings.wled_devices)==2)
            report['board_flow']={'multiple_selected_added':{b['ip'] for b in repo.load().wled_devices}=={'192.0.2.1','192.0.2.2'}}
            _spin(app,lambda:_javascript(app,window,'document.querySelectorAll("#board-list input").length')==2)
            _javascript(app,window,'document.querySelector("#board-list input").click()')
            _spin(app,lambda:not service.settings.wled_devices[0]['enabled'])
            report['board_flow']['sync_choice_persisted']=not repo.load().wled_devices[0]['enabled']
            _javascript(app,window,'document.querySelector("#board-list input").click()')
            _spin(app,lambda:service.settings.wled_devices[0]['enabled'])
            report['board_flow']['sync_reenabled']=repo.load().wled_devices[0]['enabled']
            # Header save buttons use the real channel and must not activate
            # their surrounding disclosure when saving edited fields.
            _javascript(app,window,'''document.getElementById('input-details').open=true;
              const rising=document.getElementById('noise-rising');rising.value='9';
              rising.dispatchEvent(new Event('input',{bubbles:true}));
              const alias=document.getElementById('alias-REST');alias.value+=', pause lesson';
              alias.dispatchEvent(new Event('input',{bubbles:true}));
              const corrections=document.getElementById('corrections');corrections.value+=String.fromCharCode(10)+'pauselesson = pause lesson';
              corrections.dispatchEvent(new Event('input',{bubbles:true}));
              document.getElementById('save-noise').click();''')
            _spin(app,lambda:service.settings.noise_rising_db==9 and
                  'pause lesson' in service.settings.command_aliases['REST'])
            report['header_saves']={
                'noise_persisted':repo.load().noise_rising_db==9,
                'voice_persisted_together':'pause lesson' in repo.load().command_aliases['REST'] and
                    repo.load().command_corrections.get('pauselesson')=='pause lesson',
                'separate_voice_save_removed':_javascript(app,window,
                    'document.querySelectorAll("#voice-form button[type=submit]").length===0'),
                'noise_did_not_collapse':_javascript(app,window,'document.getElementById("input-details").open'),
            }
            _javascript(app,window,'''document.getElementById('audio-details').open=true;
              const volume=document.getElementById('audio-volume');volume.value='45';
              volume.dispatchEvent(new Event('input',{bubbles:true}));
              const fade=document.getElementById('audio-fade');fade.value='300';
              fade.dispatchEvent(new Event('input',{bubbles:true}));
              document.getElementById('save-audio').click();''')
            _spin(app,lambda:service.settings.audio_volume_percent==45 and service.settings.audio_fade_ms==300)
            report['header_saves'].update({
                'audio_persisted':repo.load().audio_volume_percent==45 and repo.load().audio_fade_ms==300,
                'audio_did_not_collapse':_javascript(app,window,'document.getElementById("audio-details").open'),
            })
            report['settings_structure']=json.loads(_javascript(app,window,'''JSON.stringify({
              output_button_removed:!document.getElementById('output-toggle'),
              playback_at_top:Boolean(document.querySelector('#audio-details>.region-content>.audio-toolbar')),
              playback_not_collapsed:!document.querySelector('#audio-details details'),
              header_save_positions:['save-noise','save-audio'].every(id=>document.getElementById(id).parentElement.tagName==='SUMMARY')
            })'''))
            # Mode QA switches rapidly; let the requested 1.4 s transition settle.
            deadline=time.monotonic()+1.5
            _spin(app,lambda:time.monotonic()>=deadline)
            for language in ('zh_TW','en_US'):
                _javascript(app,window,f'window.classroom.command("language",{{language:"{language}"}})')
                _spin(app,lambda:_javascript(app,window,'document.documentElement.lang')==('en' if language=='en_US' else 'zh-Hant'))
                report['languages'].append(language)
                for width,height in ((1040,720),(700,520),(390,740)):
                    window.setMinimumSize(0,0)
                    window.resize(width,height)
                    _spin(app,lambda:_javascript(app,window,f"innerWidth==={width}&&innerHeight==={height}"))
                    _javascript(app,window,'window.classroom.navigate("classroom")')
                    app.processEvents()
                    geometry=json.loads(_javascript(app,window,'''JSON.stringify((()=>{
                      const page=document.getElementById('page-classroom'),r=page.getBoundingClientRect();
                      const controls=Array.from(page.querySelectorAll('button,canvas,#current-mode,#transcript')).filter(e=>e.getClientRects().length);
                      return {width:innerWidth,height:innerHeight,scrollX:page.scrollWidth-page.clientWidth,scrollY:page.scrollHeight-page.clientHeight,
                        clipped:controls.filter(e=>{const b=e.getBoundingClientRect();return b.left<r.left-.5||b.right>r.right+.5||b.top<r.top-.5||b.bottom>r.bottom+.5||b.width<1||b.height<1}).map(e=>e.id||e.dataset.mode),
                        minimumModeHeight:Math.min(...Array.from(document.querySelectorAll('[data-mode]'),e=>e.getBoundingClientRect().height))};})())'''))
                    geometry.update({'language':language,'requested_width':width,'requested_height':height,
                                     'actual_viewport':{'width':geometry['width'],'height':geometry['height']}})
                    report['layouts'].append(geometry)
                    if captures:
                        deadline=time.monotonic()+.2
                        _spin(app,lambda:time.monotonic()>=deadline)
                        window.view.grab().save(str(path.parent/f'classroom-{language}-{width}.png'))
                window.resize(1040,720)
                # Settings headers retain their title and save action on narrow
                # windows in both languages, with no overlap or clipped button.
                _javascript(app,window,'window.classroom.navigate("connection")')
                for width in (1040,700,390):
                    window.resize(width,720)
                    _spin(app,lambda:_javascript(app,window,f"innerWidth==={width}&&innerHeight===720"))
                    app.processEvents()
                    headers=json.loads(_javascript(app,window,'''JSON.stringify(['input-details','audio-details'].map(id=>{
                      const s=document.querySelector(`#${id}>summary`),r=s.getBoundingClientRect(),
                        h=s.querySelector('h2').getBoundingClientRect(),b=s.querySelector('button').getBoundingClientRect();
                      return {page:'settings-header',id,width:innerWidth,height:innerHeight,scrollX:s.scrollWidth-s.clientWidth,
                        clipped:b.right>r.right+.5||b.left<h.right-1};}))'''))
                    for header in headers:
                        header.update({'requested_width':width,'requested_height':720,'language':language,
                                       'actual_viewport':{'width':header['width'],'height':header['height']}})
                    report['layouts'].extend(headers)
                window.resize(1040,720)
                for page in ('connection','timers'):
                    _javascript(app,window,f'window.classroom.navigate("{page}")')
                    app.processEvents()
                    overflow=_javascript(app,window,f'document.getElementById("page-{page}").scrollWidth-document.getElementById("page-{page}").clientWidth')
                    report['layouts'].append({'page':page,'language':language,'scrollX':overflow})
                    if captures:
                        if page=='connection':
                            _javascript(app,window,'document.getElementById("voice-details").open=true;document.getElementById("audio-details").open=true;')
                        deadline=time.monotonic()+.2
                        _spin(app,lambda:time.monotonic()>=deadline)
                        window.view.grab().save(str(path.parent/f'{page}-{language}.png'))
                        if page=='connection':
                            _javascript(app,window,'document.querySelector("#noise-form details").open=true')
                            for region,target in (('noise','noise-form'),('voice','voice-details'),('audio','audio-details')):
                                _javascript(app,window,f'''(()=>{{const page=document.getElementById('page-connection'),target=document.getElementById('{target}');page.scrollTop+=target.getBoundingClientRect().top-page.getBoundingClientRect().top-16;}})()''')
                                deadline=time.monotonic()+.15
                                _spin(app,lambda:time.monotonic()>=deadline)
                                window.view.grab().save(str(path.parent/f'{region}-{language}.png'))
            _javascript(app,window,'''window.classroom.navigate('timers');
              document.getElementById('question_seconds').value='12';
              document.getElementById('rest_reminder_seconds').value='25';
              document.getElementById('timers-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));''')
            _spin(app,lambda:service.settings.question_seconds==12 and service.settings.rest_reminder_seconds==25)
            report['timer_form_persisted']=repo.load().question_seconds==12 and repo.load().rest_reminder_seconds==25
            _javascript(app,window,'''window.classroom.navigate('connection');document.getElementById('voice-details').open=true;
              document.getElementById('alias-NOTICE').value='notice, attention, listen, 注意';
              document.getElementById('voice-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));''')
            _spin(app,lambda:'listen' in service.settings.command_aliases['NOTICE'])
            report['custom_alias_persisted']=CommandParser(repo.load(),cooldown_seconds=0).parse('please listen to me').intent=='NOTICE'
            report['wheel_guard']=_javascript(app,window,'''Array.from(document.querySelectorAll('input,select')).every(e=>{
              const before=e.value,ev=new WheelEvent('wheel',{deltaY:-120,bubbles:true,cancelable:true});e.dispatchEvent(ev);return before===e.value&&ev.defaultPrevented;})''')
            report['disclosures']=_javascript(app,window,'document.querySelectorAll("details>summary").length')
            report['no_numeric_spinners']=_javascript(app,window,'document.querySelectorAll("input[type=number]").length===0')
            report['live_voice_custom_fields']=_javascript(app,window,'document.querySelectorAll("#alias-fields input").length===7')
            report['audio_fields']=_javascript(app,window,'document.querySelectorAll("#audio-files .audio-file").length')
            report['music_reactive_switches']=_javascript(app,window,'document.querySelectorAll("input[id^=audio-reactive-]").length')
            _javascript(app,window,'document.getElementById("audio-reactive-REST").click()')
            _spin(app,lambda:service.settings.audio_reactive_modes['rest'])
            report['music_reactive_persisted']=repo.load().audio_reactive_modes['rest']
            _javascript(app,window,'window.classroom.navigate("classroom")')
            before=_javascript(app,window,'window.classroom.frameChanges')
            deadline=time.monotonic()+.25
            _spin(app,lambda:time.monotonic()>=deadline)
            report['preview_frame_changes']=_javascript(app,window,'window.classroom.frameChanges')
            report['preview_frame_bytes']=len(service._last_frame)
            report['preview_live']=report['preview_frame_changes']>before
            report['font_loaded']=_javascript(app,window,'document.fonts.check("14px Classroom")')
            report['errors']=window.page.errors
            window.view.grab().save(str(path.with_suffix('.png')))
            report['passed']=(report['page_count']==3 and len(report['timer_fields'])==5 and
                len(report['modes'])==7 and all(k==v for k,v in report['modes'].items()) and
                all(report['voice_flow'].values()) and all(report['board_flow'].values()) and
                all(report['header_saves'].values()) and all(report['settings_structure'].values()) and report['preview_live'] and report['preview_frame_bytes']==192 and
                report['wheel_guard'] and report['no_numeric_spinners'] and report['live_voice_custom_fields'] and
                report['audio_fields']==7 and report['music_reactive_switches']==7 and report['music_reactive_persisted'] and all(report['audio_decoders'].values()) and report['rest_music']['loaded'] and report['font_loaded'] and not report['errors'] and report['timer_form_persisted'] and report['custom_alias_persisted'] and
                all(item.get('scrollX',0)<=0 and item.get('scrollY',0)<=0 and not item.get('clipped') for item in report['layouts']))
        except Exception as exc:
            report['errors'].append(str(exc));report['passed']=False
        finally:
            window.close()
            _spin(app,lambda:window._shutdown_complete,35)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if not report['passed']:
            raise RuntimeError(f'Web UI acceptance failed: {path}')
    return report


def main():
    app=QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window=ClassroomWindow()
    window.show()
    return app.exec()


if __name__=='__main__':
    flag=sys.argv[1].split('=',1)[0] if len(sys.argv)>1 else ''
    report=sys.argv[2] if len(sys.argv)==3 else sys.argv[1].split('=',1)[1] if len(sys.argv)==2 and '=' in sys.argv[1] else None
    if report and flag in ('--self-test-report','--ui-smoke-report'):
        (self_test_report if flag=='--self-test-report' else ui_smoke_report)(report)
    else:
        raise SystemExit(main())
