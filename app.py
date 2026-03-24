from PySide6.QtWidgets import QApplication, QPushButton, QLineEdit, QDialog, QVBoxLayout

import sys

class Dialog(QDialog):
    def __init__(self, parent=None):
        super(Dialog, self).__init__(parent)

        #Setting the title for the Application window        
        self.setWindowTitle("My First Dialog Box")
        
        #Initializing the widgets
        self.edit = QLineEdit("")
        self.button = QPushButton("Show greetings")

        #Creating the layout
        layout = QVBoxLayout(self)
        layout.addWidget(self.edit)
        layout.addWidget(self.button)

        self.setLayout(layout)

        self.button.clicked.connect(self.greeting)

    def greeting(self):
        print(f'Hello {self.edit.text()}!')

if __name__ == '__main__':
    app = QApplication(sys.argv)
    dialog = Dialog()
    dialog.show()

    app.exec()