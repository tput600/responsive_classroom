import unittest
import re
from classroom_i18n import _TEXT, translate


class TranslationTests(unittest.TestCase):
    def test_traditional_chinese_and_unknown_languages_keep_source_text(self):
        for language in ("zh_TW", "ja_JP"):
            self.assertEqual(translate("連線與收音", language), "連線與收音")

    def test_navigation_and_six_modes_have_short_english_labels(self):
        self.assertEqual(
            [translate(value, "en_US") for value in ("課堂", "連線與收音", "計時")],
            ["Classroom", "Connection", "Timers"],
        )
        self.assertEqual(
            [translate(value, "en_US") for value in ("待機", "請注意", "休息", "提問", "答對", "答錯")],
            ["Standby", "Attention", "Rest", "Question", "Correct", "Wrong"],
        )
        self.assertEqual(
            [translate(value, "en_US") for value in (
                "燈板未連線", "燈板已連線", "燈板連線中", "燈板暫停", "燈板連線異常",
                "麥克風準備中", "麥克風正常", "麥克風異常", "語音已關閉", "語音已開啟",
                "語音準備中", "內建麥克風", "開啟語音偵測", "關閉語音偵測",
            )],
            ["Panel offline", "Panel connected", "Connecting panel", "Panel paused", "Panel error",
             "Mic preparing", "Mic ready", "Mic error", "Voice off", "Voice on",
             "Voice preparing", "Built-in mic", "Enable voice", "Disable voice"],
        )

    def test_timer_and_countdown_templates_keep_the_live_seconds(self):
        self.assertEqual(translate("提問 10 秒", "en_US"), "Question · 10 sec")
        self.assertEqual(translate("提問 10秒", "en_US"), "Question · 10 sec")
        self.assertEqual(translate("答對 5秒", "en_US"), "Correct · 5 sec")
        self.assertEqual(translate("答錯 5秒", "en_US"), "Wrong · 5 sec")
        self.assertEqual(translate("休息 · 600 秒", "en_US"), "Break · 600 sec")
        self.assertEqual(translate("剩餘 9 秒", "en_US"), "Remaining 9 sec")
        self.assertEqual(translate("語音閒置 · 30 秒", "en_US"), "Voice idle · 30 sec")
        self.assertEqual(translate("音量上升 · -42.5 dBFS", "en_US"), "Rising noise · -42.5 dBFS")
        self.assertEqual(translate("語音閒置回待機", "en_US"), "Voice idle reset")
        self.assertEqual(translate("設定模式時長，以及語音控制閒置後回待機的秒數。", "en_US"),
                         "Set mode durations and seconds before voice control returns to standby when idle.")
        self.assertEqual(translate("無有效指令，已回待機", "en_US"),
                         "No valid command; returned to standby")

    def test_user_aliases_and_transcripts_are_preserved(self):
        alias = "課堂提問，class question"
        transcript = "辨識：Class question."
        self.assertEqual(translate(alias, "en_US"), alias)
        self.assertEqual(translate(transcript, "en_US"), "Recognized: Class question.")
        self.assertEqual(translate("修正：class question · 已套用", "en_US"), "Corrected: class question · Applied")
        self.assertEqual(translate("待機的別名", "en_US"), "Aliases for Standby")

    def test_common_dynamic_statuses_translate_but_keep_error_details(self):
        self.assertEqual(translate("搜尋完成，找到 2 塊燈板。", "en_US"), "Scan complete. Found 2 panels.")
        self.assertEqual(
            translate("連線未成功，會自動重試：timed out", "en_US"),
            "Connection failed; retrying automatically: timed out",
        )
        self.assertEqual(translate("請檢查輸入裝置", "en_US"), "Check input device")
        self.assertEqual(translate("無法啟用燈板", "en_US"), "Could not enable panel")
        self.assertEqual(translate("無法啟用燈板：network timeout", "en_US"),
                         "Could not enable panel: network timeout")
        self.assertEqual(translate("network unreachable", "en_US"), "network unreachable")

    def test_static_dictionary_values_are_english(self):
        for source, english in _TEXT.items():
            with self.subTest(source=source):
                self.assertNotEqual(source, english)
                self.assertIsNone(re.search(r"[\u3400-\u9fff]", english))

    def test_reference_dashboard_copy_has_english_translations(self):
        labels = (
            "課堂小幫手", "教室互動控制", "燈板已連線", "燈板未連線", "燈板連線中",
            "燈板暫停", "燈板連線異常", "麥克風正常", "麥克風準備中", "麥克風異常",
            "語音已關閉", "語音已開啟", "語音準備中", "尚未偵測", "內建麥克風",
            "點擊下列按鈕，立即在燈板顯示對應效果", "顯示待機圖案", "吸引學生注意",
            "短暫休息時間", "進入提問倒數", "正確回應提示", "錯誤回應提示",
            "即時預覽：8×8 LED 燈板", "收音輸入", "最新辨識文字", "（尚無語音輸入）",
            "未設定", "連線狀態會自動更新", "手動 IP", "已連線的燈板", "環境音量",
            "語音指令", "變更裝置後請重新校準。", "先連上燈板，再選擇麥克風並完成校準。",
            "額外指令前綴（可選）",
            "自訂課堂時間", "提問與回應提示會自動回到原模式。", "顯示時間", "休息時間",
            "收尾提醒時間", "模式結束時的暖黃提示。", "語音閒置回待機",
            "設定模式時長，以及語音控制閒置後回待機的秒數。", "無有效指令，已回待機",
        )
        for source in labels:
            with self.subTest(source=source):
                english = translate(source, "en_US")
                self.assertNotEqual(english, source)
                self.assertIsNone(re.search(r"[\u3400-\u9fff]", english))


if __name__ == "__main__":
    unittest.main()
