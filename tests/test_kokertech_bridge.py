import unittest
from unittest.mock import patch, mock_open
import kokertech_bridge


class TestKokertechBridge(unittest.TestCase):

    @patch('os.makedirs')
    @patch('os.path.exists', return_value=False)
    def test_create_folder_success(self, mock_exists, mock_makedirs):
        intent = {"action": "CREATE_FOLDER", "path": "test_folder"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("Created folder", result)
        mock_makedirs.assert_called_once()

    def test_create_folder_missing_path(self):
        intent = {"action": "CREATE_FOLDER"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("Missing 'path'", result)

    @patch('builtins.open', new_callable=mock_open)
    @patch('os.makedirs')
    def test_write_file_success(self, mock_makedirs, mock_file):
        intent = {"action": "WRITE_FILE", "filename": "test_script.py", "content": "print('hello')"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("Successfully wrote to", result)
        mock_makedirs.assert_called_once()
        mock_file.assert_called_once()
        mock_file().write.assert_called_once_with("print('hello')")

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data="File content here")
    def test_read_file_success(self, mock_file, mock_exists):
        intent = {"action": "READ_FILE", "filename": "test_doc.txt"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("Read", result)
        self.assertIn("File content here", result)

    @patch('os.path.exists', return_value=False)
    def test_read_file_not_found(self, mock_exists):
        intent = {"action": "READ_FILE", "filename": "missing.txt"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("File not found", result)

    @patch('os.remove')
    @patch('os.path.isdir', return_value=False)
    @patch('os.path.exists', return_value=True)
    def test_delete_file_success(self, mock_exists, mock_isdir, mock_remove):
        intent = {"action": "DELETE_FILE", "path": "old_file.txt"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("Successfully deleted", result)
        mock_remove.assert_called_once()

    def test_unknown_action(self):
        intent = {"action": "INVALID_ACTION_NAME"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("unknown or not registered", result)

if __name__ == '__main__':
    unittest.main()