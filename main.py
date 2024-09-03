import requests, json, subprocess, sys, logging, time, psutil
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox, QProgressDialog, QPushButton, QFileDialog
from PyQt5.QtCore import QThread, pyqtSignal
from ui_llm import Ui_MainWindow

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

API_URL = "http://127.0.0.1:11434/api/chat"
COMMAND_LIST = "ollama list"
OLLAMA_SERVE_COMMAND = ["ollama", "serve"]


def is_ollama_running():
    """检查ollama服务是否正在运行"""
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.info['name'] == 'ollama.exe':
            return True
    return False


class OllamaServiceStarter(QThread):
    """后台线程用于启动ollama服务"""
    started = pyqtSignal(bool)  # 信号表示服务是否成功启动

    def run(self):
        try:
            subprocess.Popen(OLLAMA_SERVE_COMMAND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # 假设ollama服务需要一段时间来准备就绪，这里等待5秒
            time.sleep(5)
            if is_ollama_running():
                self.started.emit(True)
            else:
                self.started.emit(False)
        except Exception as e:
            logging.error(f"Error starting ollama service: {e}")
            self.started.emit(False)


class SafeExternalCommandExecutor:
    def __init__(self, command):
        self.command = command

    def execute(self):
        try:
            output = subprocess.check_output(self.command, shell=True, text=True)
            lines = output.strip().split('\n')
            return [line.split()[0] for line in lines[1:] if line.split()]
        except subprocess.CalledProcessError as e:
            logging.warning(f"执行命令失败: {str(e)}")
            return None
        except Exception as e:
            logging.error(f"未知错误: {str(e)}")
            return None


class ResponseFetcher(QThread):
    response_ready = pyqtSignal(str)
    progress_update = pyqtSignal(int)

    def __init__(self, model, message):
        super().__init__()
        self.model = model
        self.message = message

    def run(self):
        data = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": f"请用中文回答:{self.message}"
                }
            ]
        }
        try:
            response = requests.post(API_URL, data=json.dumps(data)).content
            self.response_ready.emit(response.decode('utf-8'))
        except requests.exceptions.RequestException as e:
            self.response_ready.emit(f"网络请求失败: {str(e)}")
        finally:
            self.progress_update.emit(100)


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        self.index = 0
        self.setupUi(self)
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("大模型测试")
        self.setFixedSize(self.width(), self.height())
        self.lineEdit.returnPressed.connect(self.send_message)
        self.pushButton.clicked.connect(self.send_message)

        self.exportButton = QPushButton("导出", self)
        # self.exportButton.move(10, 10)  # 将按钮放在左上角，根据需要调整位置
        self.gridLayout.addWidget(self.exportButton, 3, 6, 1, 1)
        self.exportButton.clicked.connect(self.export_text)
        # 调整textEdit宽度
        self.gridLayout.addWidget(self.textEdit, 2, 0, 1, 7)

        # 确保ollama服务启动
        self.ensure_ollama_service()

    def export_text(self):
        """导出textEdit中的内容到文本文档"""
        dialog = QFileDialog(self)
        dialog.setNameFilter("Text files (*.txt)")
        if dialog.exec():
            file_path = dialog.selectedFiles()[0]
            with open(file_path, 'w', encoding='utf-8') as f:
                text = self.textEdit.toPlainText()
                f.write(text)
                QMessageBox.information(self, "成功", "内容已导出到文件：" + file_path)
        else:
            QMessageBox.warning(self, "警告", "请选择一个文件来保存内容")

    def ensure_ollama_service(self):
        """确保ollama服务已启动"""
        if not is_ollama_running():
            self.ollama_starter = OllamaServiceStarter()
            self.ollama_starter.started.connect(self.on_ollama_started)
            self.ollama_starter.start()
        else:
            self.get_llm_list()

    def on_ollama_started(self, started):
        """ollama服务启动后的回调"""
        if started:
            self.get_llm_list()
        else:
            QMessageBox.warning(self, "警告", "无法启动ollama服务，请检查配置。")

    def get_llm_list(self):
        """获取LLM模型列表"""
        executor = SafeExternalCommandExecutor(COMMAND_LIST)
        self.names = executor.execute()
        if self.names is None:
            QMessageBox.warning(self, "警告", "获取模型列表失败")
        else:
            for item in self.names:
                self.comboBox.addItem(item)

    def show_progress_dialog(self):
        self.progress_dialog = QProgressDialog(self)
        self.progress_dialog.setWindowTitle('等待')
        self.progress_dialog.setLabelText("正在获取回复...")
        self.progress_dialog.setRange(0, 0)
        self.progress_dialog.setCancelButton(None)
        self.progress_dialog.show()

    def send_message(self):
        message = self.lineEdit.text()
        if message == "":
            QMessageBox.warning(self, "警告", "请输入内容")
            return
        self.label_content.setText(message)
        self.index += 1
        self.textEdit.append(f'问题{self.index}:' + self.lineEdit.text() + '\n')
        self.textEdit.moveCursor(QTextCursor.End)
        self.fetch_response()

    def fetch_response(self):
        self.show_progress_dialog()
        self.fetch_thread = ResponseFetcher(self.comboBox.currentText(), self.lineEdit.text())
        self.fetch_thread.response_ready.connect(self.handle_response)
        self.fetch_thread.progress_update.connect(self.progress_dialog.setValue)
        self.fetch_thread.finished.connect(self.progress_dialog.hide)
        self.fetch_thread.start()

    def handle_response(self, response):
        ans_lists = response.split('\n')
        for ans in ans_lists:
            if ans.startswith('{'):
                try:
                    ans_dict = json.loads(ans)
                    if 'content' in ans_dict['message']:
                        cont = ans_dict['message']['content'].replace('\n\n', '\n')
                        self.textEdit.insertPlainText(cont)
                except json.JSONDecodeError:
                    logging.warning(f"JSON解析失败: {ans}")
        self.textEdit.append('\n')
        self.lineEdit.clear()


def start_application():
    app = QApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    start_application()
