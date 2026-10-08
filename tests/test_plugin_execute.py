"""
Tests for individual plugin execute() functions.

Covers all 28 plugins in plugins/ directory. Tests:
- Module-level exports (COMMAND_NAME, SCHEMA, PLUGIN_METADATA)
- Missing parameters handling
- Success paths with mocked dependencies
- Security edge cases (path traversal for filesystem plugins)
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


def _make_temp_workspace():
    tmpdir = tempfile.mkdtemp()
    return tmpdir


class TestCreateFolderPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.create_folder as p
        self.assertEqual(p.COMMAND_NAME, 'CREATE_FOLDER')
        self.assertIn('action', p.SCHEMA)
        self.assertIn('path', p.SCHEMA)

    def test_missing_path(self):
        from plugins.create_folder import execute
        self.assertIn('Missing', execute({}))

    def test_path_traversal_blocked(self):
        from plugins.create_folder import execute
        self.assertIn('Security', execute({'path': '../../evil'}))

    def test_success(self):
        import plugins.create_folder
        tmp = _make_temp_workspace()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        plugins.create_folder.WORKSPACE_DIR = tmp
        result = plugins.create_folder.execute({'path': 'new_folder'})
        self.assertIn('Created folder', result)
        self.assertTrue(os.path.isdir(os.path.join(tmp, 'new_folder')))


class TestReadFilePlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.read_file as p
        self.assertEqual(p.COMMAND_NAME, 'READ_FILE')

    def test_missing_path(self):
        from plugins.read_file import execute
        self.assertIn('Missing', execute({}))

    def test_file_not_found(self):
        from plugins.read_file import execute
        self.assertIn('found', execute({'path': 'nonexistent.txt'}))

    def test_path_traversal_blocked(self):
        from plugins.read_file import execute
        self.assertIn('Security', execute({'path': '../../evil'}))

    def test_success(self):
        import plugins.read_file
        tmp = _make_temp_workspace()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        fpath = os.path.join(tmp, 'test.txt')
        with open(fpath, 'w') as f:
            f.write('hello world')
        plugins.read_file.WORKSPACE_DIR = tmp
        result = plugins.read_file.execute({'path': 'test.txt'})
        self.assertIn('hello world', result)


class TestWriteFilePlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.write_file as p
        self.assertEqual(p.COMMAND_NAME, 'WRITE_FILE')

    def test_missing_path(self):
        from plugins.write_file import execute
        self.assertIn('Missing', execute({}))

    def test_path_traversal_blocked(self):
        from plugins.write_file import execute
        self.assertIn('Security', execute({'path': '../../evil', 'content': 'x'}))

    def test_success(self):
        import plugins.write_file
        tmp = _make_temp_workspace()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        plugins.write_file.WORKSPACE_DIR = tmp
        result = plugins.write_file.execute({'path': 'out.txt', 'content': 'hello'})
        self.assertIn('Success', result)
        fpath = os.path.join(tmp, 'out.txt')
        self.assertTrue(os.path.isfile(fpath))
        with open(fpath) as f:
            self.assertEqual(f.read(), 'hello')

    def test_success_with_filename_key(self):
        import plugins.write_file
        tmp = _make_temp_workspace()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        plugins.write_file.WORKSPACE_DIR = tmp
        result = plugins.write_file.execute({'filename': 'out2.txt', 'content': 'world'})
        self.assertIn('Success', result)


class TestDeleteFilePlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.delete_file as p
        self.assertEqual(p.COMMAND_NAME, 'DELETE_FILE')

    def test_missing_path(self):
        from plugins.delete_file import execute
        self.assertIn('Missing', execute({}))

    def test_path_traversal_blocked(self):
        from plugins.delete_file import execute
        self.assertIn('Security', execute({'path': '../../evil'}))

    def test_success(self):
        import plugins.delete_file
        tmp = _make_temp_workspace()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        fpath = os.path.join(tmp, 'todel.txt')
        with open(fpath, 'w') as f:
            f.write('x')
        plugins.delete_file.WORKSPACE_DIR = tmp
        result = plugins.delete_file.execute({'path': 'todel.txt'})
        self.assertIn('Success', result)
        self.assertFalse(os.path.exists(fpath))


class TestListFilesPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.list_files as p
        self.assertEqual(p.COMMAND_NAME, 'LIST_FILES')

    def test_path_traversal_blocked(self):
        from plugins.list_files import execute
        self.assertIn('Security', execute({'path': '../../evil'}))

    def test_success(self):
        import plugins.list_files
        tmp = _make_temp_workspace()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        with open(os.path.join(tmp, 'a.txt'), 'w') as f:
            f.write('')
        with open(os.path.join(tmp, 'b.py'), 'w') as f:
            f.write('')
        plugins.list_files.WORKSPACE_DIR = tmp
        result = plugins.list_files.execute({'path': ''})
        self.assertIn('a.txt', result)
        self.assertIn('b.py', result)


class TestRenameFilePlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.rename_file as p
        self.assertEqual(p.COMMAND_NAME, 'RENAME_FILE')

    def test_missing_path(self):
        from plugins.rename_file import execute
        self.assertIn('Missing', execute({}))

    def test_missing_new_name(self):
        from plugins.rename_file import execute
        self.assertIn('Missing', execute({'source': 'x.txt'}))

    def test_path_traversal_blocked(self):
        from plugins.rename_file import execute
        self.assertIn('Security', execute({'source': '../../evil', 'destination': 'ok.txt'}))

    def test_success(self):
        import plugins.rename_file
        tmp = _make_temp_workspace()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        fpath = os.path.join(tmp, 'old.txt')
        with open(fpath, 'w') as f:
            f.write('x')
        plugins.rename_file.WORKSPACE_DIR = tmp
        result = plugins.rename_file.execute({'source': 'old.txt', 'destination': 'new.txt'})
        self.assertIn('Success', result)
        self.assertFalse(os.path.exists(os.path.join(tmp, 'old.txt')))
        self.assertTrue(os.path.isfile(os.path.join(tmp, 'new.txt')))


class TestStoreMemoryPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.store_memory as p
        self.assertEqual(p.COMMAND_NAME, 'STORE_MEMORY')

    def test_missing_content(self):
        from plugins.store_memory import execute
        self.assertIn('Missing', execute({}))

    @patch('memory_vault.store_memory', return_value=42)
    def test_success(self, mock_store):
        from plugins.store_memory import execute
        result = execute({'content': 'something important', 'importance': 8})
        self.assertIn('Node ID', result)
        self.assertIn('42', result)
        mock_store.assert_called_once()
        args, _ = mock_store.call_args
        self.assertIn('something important', args[0])

    @patch('memory_vault.store_memory')
    def test_default_importance(self, mock_store):
        mock_store.return_value = 1
        from plugins.store_memory import execute
        execute({'content': 'test'})
        mock_store.assert_called_once()
        args, kwargs = mock_store.call_args
        self.assertIn('test', args[0])
        self.assertEqual(kwargs.get('importance'), 5)


class TestSemanticSearchPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.semantic_search as p
        self.assertEqual(p.COMMAND_NAME, 'SEMANTIC_SEARCH')

    def test_missing_query(self):
        from plugins.semantic_search import execute
        self.assertIn('Missing', execute({}))

    @patch('memory_vault.semantic_search', return_value=[])
    def test_success_empty(self, mock_search):
        from plugins.semantic_search import execute
        result = execute({'query': 'something'})
        self.assertIn('No relevant memories', result)
        mock_search.assert_called_once()

    @patch('memory_vault.semantic_search',
           return_value=[(1, 'concept', 'found', 0.95)])
    def test_success_with_results(self, mock_search):
        from plugins.semantic_search import execute
        result = execute({'query': 'something'})
        self.assertIn('Node ID: 1', result)
        self.assertIn('found', result)
        self.assertIn('0.95', result)


class TestLinkNodesPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.link_nodes as p
        self.assertEqual(p.COMMAND_NAME, 'LINK_NODES')

    def test_missing_ids(self):
        from plugins.link_nodes import execute
        self.assertIn('Missing', execute({}))
        self.assertIn('Missing', execute({'source_id': 1}))

    @patch('memory_vault.link_memories')
    def test_success(self, mock_link):
        from plugins.link_nodes import execute
        result = execute({'source_id': 1, 'target_id': 2, 'relationship_type': 'DEPENDS_ON'})
        self.assertIn('Knowledge Graph updated', result)
        mock_link.assert_called_with(1, 2, relationship_type='DEPENDS_ON')

    @patch('memory_vault.link_memories')
    def test_default_relationship(self, mock_link):
        from plugins.link_nodes import execute
        execute({'source_id': 1, 'target_id': 2})
        mock_link.assert_called_with(1, 2, relationship_type='RELATES_TO')


class TestLogBiasPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.log_bias as p
        self.assertEqual(p.COMMAND_NAME, 'LOG_BIAS')

    @patch('memory_vault.log_bias')
    def test_success(self, mock_log):
        from plugins.log_bias import execute
        result = execute({'agent_id': 'Eva', 'bias_type': 'confirmation', 'confidence_score': 80, 'description': 'test'})
        self.assertIn('SCBE Updated', result)
        mock_log.assert_called_with('Eva', 'confirmation', 80, 'test')

    @patch('memory_vault.log_bias')
    def test_default_values(self, mock_log):
        from plugins.log_bias import execute
        execute({})
        mock_log.assert_called_with('Executive', 'Unknown Bias', 50, '')


class TestLogGrowthPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.log_growth as p
        self.assertEqual(p.COMMAND_NAME, 'LOG_GROWTH')

    @patch('memory_vault.log_growth')
    def test_success(self, mock_log):
        from plugins.log_growth import execute
        result = execute({'agent_id': 'Eva', 'event_description': 'realization', 'energy_shift': 12.5})
        self.assertIn('Growth Arc Updated', result)
        mock_log.assert_called_with('Eva', 'realization', 12.5)

    @patch('memory_vault.log_growth')
    def test_default_values(self, mock_log):
        from plugins.log_growth import execute
        execute({})
        mock_log.assert_called_with('Executive', 'Unknown growth event', 0.0)


class TestIndexDocumentPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.index_document as p
        self.assertEqual(p.COMMAND_NAME, 'INDEX_DOCUMENT')

    def test_missing_path(self):
        from plugins.index_document import execute
        self.assertIn('Missing', execute({}))

    def test_path_traversal_blocked(self):
        from plugins.index_document import execute
        self.assertIn('Security', execute({'path': '../../evil'}))

    def test_file_not_found(self):
        from plugins.index_document import execute
        self.assertIn('not found', execute({'path': 'nonexistent.txt'}))

    def test_success(self):
        import plugins.index_document
        with patch('memory_vault.store_memory', return_value=99):
            tmp = _make_temp_workspace()
            self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
            fpath = os.path.join(tmp, 'doc.txt')
            with open(fpath, 'w') as f:
                f.write('Hello world content here')
            plugins.index_document.WORKSPACE_DIR = tmp
            result = plugins.index_document.execute({'path': 'doc.txt'})
            self.assertIn('indexed into 1 vector chunks', result)
            self.assertIn('99', result)


class TestFetchWebPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.fetch_web as p
        self.assertEqual(p.COMMAND_NAME, 'FETCH_WEB')

    def test_missing_url(self):
        from plugins.fetch_web import execute
        self.assertIn('Missing', execute({}))

    @patch('requests.get')
    def test_success(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, text='<html>Hello</html>')
        from plugins.fetch_web import execute
        result = execute({'url': 'http://example.com'})
        self.assertIn('Hello', result)
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertIn('http://example.com', args)
        self.assertEqual(kwargs.get('timeout'), 15)

    @patch('requests.get')
    def test_non_200(self, mock_get):
        mock_get.return_value = MagicMock(status_code=404, text='Not Found')
        mock_get.return_value.raise_for_status.side_effect = Exception('HTTP 404')
        from plugins.fetch_web import execute
        result = execute({'url': 'http://example.com/404'})
        self.assertIn('failed', result.lower())


class TestSearchWebPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.search_web as p
        self.assertEqual(p.COMMAND_NAME, 'SEARCH_WEB')

    def test_missing_query(self):
        from plugins.search_web import execute
        self.assertIn('Missing', execute({}))

    @patch('requests.get')
    @patch('plugins.search_web.BeautifulSoup')
    def test_success(self, mock_bs, mock_get):
        mock_get.return_value = MagicMock(status_code=200, text='<html></html>')
        mock_soup = MagicMock()
        mock_a1 = MagicMock()
        mock_a1.text = 'Result 1'
        mock_a2 = MagicMock()
        mock_a2.text = 'Result 2'
        mock_soup.find_all.return_value = [mock_a1, mock_a2]
        mock_bs.return_value = mock_soup
        from plugins.search_web import execute
        result = execute({'query': 'test query'})
        self.assertIn('Result 1', result)
        self.assertIn('Result 2', result)

    @patch('requests.get')
    @patch('plugins.search_web.BeautifulSoup')
    def test_no_results(self, mock_bs, mock_get):
        mock_get.return_value = MagicMock(status_code=200, text='<html></html>')
        mock_soup = MagicMock()
        mock_soup.find_all.return_value = []
        mock_bs.return_value = mock_soup
        from plugins.search_web import execute
        result = execute({'query': 'something'})
        self.assertIn('No clear results', result)


class TestClassifyIntentPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.classify_intent as p
        self.assertEqual(p.COMMAND_NAME, 'CLASSIFY_INTENT')

    def test_missing_text(self):
        from plugins.classify_intent import execute
        self.assertIn('Missing', execute({}))

    @patch('plugins.classify_intent.os.path.exists', return_value=True)
    @patch('plugins.classify_intent.joblib.load')
    def test_classification(self, mock_load, mock_exists):
        # A minimal class that behaves like a numpy array for max() and .tolist()
        class _FakeArray(list):
            def tolist(self):
                return list(self)

        mock_model = MagicMock()
        mock_model.predict.return_value = ['SEARCH_WEB']
        mock_model.predict_proba.return_value = [_FakeArray([0.1, 0.1, 0.1, 0.1, 0.6, 0.0])]
        mock_model.classes_ = _FakeArray(['Greeting', 'Support', 'Yes', 'No', 'SEARCH_WEB', 'Goodbye'])
        mock_load.return_value = mock_model
        from plugins.classify_intent import execute
        result = execute({'text': 'search for AI news'})
        self.assertIn('SEARCH_WEB', result)


class TestDelegateTaskPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.delegate_task as p
        self.assertEqual(p.COMMAND_NAME, 'DELEGATE_TASK')

    def test_missing_task(self):
        from plugins.delegate_task import execute
        self.assertIn('Missing', execute({}))

    @patch('sub_agents.SubAgent')
    def test_delegation(self, mock_agent_cls):
        mock_agent = MagicMock()
        mock_agent.execute.return_value = 'Task done'
        mock_agent_cls.return_value = mock_agent
        from plugins.delegate_task import execute
        result = execute({'task': 'write a poem', 'persona': 'Writer'})
        self.assertIn('Task done', result)


class TestResearchTopicPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.research_topic as p
        self.assertEqual(p.COMMAND_NAME, 'RESEARCH_TOPIC')

    def test_missing_topic(self):
        from plugins.research_topic import execute
        self.assertIn('Missing', execute({}))

    @patch('plugins.research_topic.get_provider')
    @patch('plugins.research_topic.requests.get')
    def test_research(self, mock_get, mock_get_provider):
        mock_get.return_value = MagicMock(status_code=200, text='<html></html>')
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {"content": "Research results", "error": None}
        mock_get_provider.return_value = mock_provider
        from plugins.research_topic import execute
        result = execute({'topic': 'AI safety', 'depth': 'brief'})
        self.assertIn('Research results', result)


class TestToolUsePlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.tool_use as p
        self.assertEqual(p.COMMAND_NAME, 'TOOL_USE')

    @patch('sub_agents.ToolUseAgent', create=True)
    def test_execution(self, mock_agent_cls):
        mock_agent = MagicMock()
        mock_agent.execute.return_value = 'Tool output'
        mock_agent_cls.return_value = mock_agent
        from plugins.tool_use import execute
        result = execute({'task': 'do something'})
        self.assertIn('Tool output', result)
        mock_agent.execute.assert_called_once()


class TestWebhookPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.webhook as p
        self.assertEqual(p.COMMAND_NAME, 'WEBHOOK')

    def test_missing_target(self):
        from plugins.webhook import execute
        self.assertIn('Missing', execute({}))

    @patch('plugin_registry.registry.is_enabled', return_value=True)
    def test_execute_unknown_target(self, mock_enabled):
        with patch('plugin_registry.registry.plugins', {}):
            from plugins.webhook import execute
            result = execute({'target': 'NONEXISTENT'})
            self.assertIn('Unknown', result)


class TestMcpClientPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.mcp_client as p
        self.assertEqual(p.COMMAND_NAME, 'MCP_CONNECT')

    def test_missing_name(self):
        from plugins.mcp_client import execute
        result = execute({'url': 'http://localhost:9100'})
        self.assertIn('requires', result.lower())

    def test_missing_url(self):
        from plugins.mcp_client import execute
        result = execute({'name': 'test'})
        self.assertIn('requires', result.lower())

    @patch('mcp_client.MCPServerConnection')
    @patch('mcp_client.get_mcp_client')
    def test_connect(self, mock_get_client, mock_conn_cls):
        mock_client = MagicMock()
        mock_conn = MagicMock()
        mock_conn.discover_tools.return_value = ['tool1']
        mock_client.servers = {}
        mock_get_client.return_value = mock_client
        mock_conn_cls.return_value = mock_conn

        with patch('config.CONFIG', {}):
            with patch('config.save_settings'):
                from plugins.mcp_client import execute
                result = execute({'name': 'test', 'url': 'http://localhost:9100'})
                self.assertIn('Connected', result)


class TestCaptureScreenPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.capture_screen as p
        self.assertEqual(p.COMMAND_NAME, 'CAPTURE_SCREEN')

    @patch('copilot_features.ScreenCapture')
    def test_capture_success(self, mock_capture_cls):
        mock_capture = MagicMock()
        mock_capture.capture.return_value = ('screenshot.png', 'TestApp')
        mock_capture_cls.return_value = mock_capture
        from plugins.capture_screen import execute
        result = execute({})
        self.assertIn('captured', result.lower())


class TestExtractAudioPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.extract_audio as p
        self.assertEqual(p.COMMAND_NAME, 'EXTRACT_AUDIO')

    def test_missing_path(self):
        from plugins.extract_audio import execute
        self.assertIn('Missing', execute({}))

    def test_path_traversal_blocked(self):
        from plugins.extract_audio import execute
        self.assertIn('Security', execute({'path': '../../evil'}))

    def test_file_not_found(self):
        from plugins.extract_audio import execute
        self.assertIn('not found', execute({'path': 'nonexistent.mp3'}))

    def test_success(self):
        # Inject fake whisper module to avoid numba/numpy version conflicts
        import sys
        fake_whisper = MagicMock()
        fake_model = MagicMock()
        fake_model.transcribe.return_value = {'text': ' hello world '}
        fake_whisper.load_model.return_value = fake_model
        with patch.dict('sys.modules', {'whisper': fake_whisper}):
            import plugins.extract_audio
            tmp = _make_temp_workspace()
            self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
            fpath = os.path.join(tmp, 'test.mp3')
            with open(fpath, 'w') as f:
                f.write('fake audio')
            plugins.extract_audio.WORKSPACE_DIR = tmp
            result = plugins.extract_audio.execute({'path': 'test.mp3'})
            self.assertIn('Extracted Audio Transcript', result)
            self.assertIn('hello world', result)


class TestExtractImageTextPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.extract_image_text as p
        self.assertEqual(p.COMMAND_NAME, 'EXTRACT_IMAGE_TEXT')

    def test_missing_path(self):
        from plugins.extract_image_text import execute
        self.assertIn('Missing', execute({}))

    def test_path_traversal_blocked(self):
        from plugins.extract_image_text import execute
        self.assertIn('Security', execute({'path': '../../evil'}))

    @patch('pytesseract.image_to_string', return_value='ocr text')
    @patch('PIL.Image.open')
    def test_success(self, mock_open_img, mock_ocr):
        import plugins.extract_image_text
        tmp = _make_temp_workspace()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        fpath = os.path.join(tmp, 'test.png')
        with open(fpath, 'w') as f:
            f.write('fake image')
        plugins.extract_image_text.WORKSPACE_DIR = tmp
        result = plugins.extract_image_text.execute({'path': 'test.png'})
        self.assertIn('Extracted Text', result)
        self.assertIn('ocr text', result)


class TestGenerateDocxPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.generate_docx as p
        self.assertEqual(p.COMMAND_NAME, 'GENERATE_DOCX')

    def test_path_traversal_blocked(self):
        from plugins.generate_docx import execute
        self.assertIn('Security', execute({'filename': '../../evil.docx'}))

    def test_success(self):
        # Inject fake docx module to avoid ModuleNotFoundError
        import sys
        fake_docx = MagicMock()
        fake_docx.Document.return_value = MagicMock()
        with patch.dict('sys.modules', {'docx': fake_docx}):
            import plugins.generate_docx
            tmp = _make_temp_workspace()
            self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
            plugins.generate_docx.WORKSPACE_DIR = tmp
            result = plugins.generate_docx.execute({
                'filename': 'report.docx', 'title': 'My Report', 'content': 'para1\n\npara2'
            })
            self.assertIn('Successfully generated', result)
            fake_docx.Document.assert_called_once()

    def test_default_filename(self):
        # Inject fake docx module to avoid ModuleNotFoundError
        import sys
        fake_docx = MagicMock()
        fake_docx.Document.return_value = MagicMock()
        with patch.dict('sys.modules', {'docx': fake_docx}):
            import plugins.generate_docx
            tmp = _make_temp_workspace()
            self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
            plugins.generate_docx.WORKSPACE_DIR = tmp
            result = plugins.generate_docx.execute({})
            self.assertIn('Successfully generated', result)


class TestReadExcelPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.read_excel as p
        self.assertEqual(p.COMMAND_NAME, 'READ_EXCEL')

    def test_missing_path(self):
        from plugins.read_excel import execute
        self.assertIn('Missing', execute({}))

    def test_path_traversal_blocked(self):
        from plugins.read_excel import execute
        self.assertIn('Security', execute({'path': '../../evil'}))

    def test_file_not_found(self):
        from plugins.read_excel import execute
        self.assertIn('not found', execute({'path': 'nonexistent.xlsx'}))

    def test_success_csv(self):
        # Skip if tabulate not installed (to_markdown will fail)
        import importlib.util
        if importlib.util.find_spec('tabulate') is None:
            self.skipTest('tabulate not installed - required for to_markdown')
        import plugins.read_excel
        tmp = _make_temp_workspace()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        fpath = os.path.join(tmp, 'data.csv')
        with open(fpath, 'w') as f:
            f.write('A,B\n1,3\n2,4')
        plugins.read_excel.WORKSPACE_DIR = tmp
        result = plugins.read_excel.execute({'path': 'data.csv'})
        self.assertIn('Parsed Spreadsheet Data', result)
        self.assertIn('|', result)


class TestReadPdfPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.read_pdf as p
        self.assertEqual(p.COMMAND_NAME, 'READ_PDF')

    def test_missing_path(self):
        from plugins.read_pdf import execute
        self.assertIn('Missing', execute({}))

    def test_file_not_found(self):
        from plugins.read_pdf import execute
        self.assertIn('not found', execute({'path': 'nonexistent.pdf'}))

    @patch('pypdf.PdfReader')
    def test_success(self, mock_reader_cls):
        import plugins.read_pdf
        tmp = _make_temp_workspace()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        fpath = os.path.join(tmp, 'doc.pdf')
        with open(fpath, 'w') as f:
            f.write('fake pdf')
        mock_page = MagicMock()
        mock_page.extract_text.return_value = 'Hello PDF'
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_cls.return_value = mock_reader
        plugins.read_pdf.WORKSPACE_DIR = tmp
        result = plugins.read_pdf.execute({'path': 'doc.pdf'})
        self.assertIn('Read PDF', result)
        self.assertIn('Hello PDF', result)


class TestSystemStatusPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.system_status as p
        self.assertEqual(p.COMMAND_NAME, 'SYSTEM_STATUS')

    def test_status(self):
        # Inject fake psutil via sys.modules to avoid ImportError on systems without psutil
        import sys
        fake_psutil = MagicMock()
        fake_psutil.cpu_percent.return_value = 45.0
        fake_psutil.virtual_memory.return_value = MagicMock(
            percent=60.0, total=16*1024**3, available=8*1024**3
        )
        fake_psutil.disk_usage.return_value = MagicMock(
            percent=50.0, total=500*1024**3, free=250*1024**3
        )
        with patch.dict('sys.modules', {'psutil': fake_psutil}):
            from plugins.system_status import execute
            result = execute({})
            self.assertIn('CPU', result)
            self.assertIn('45.0', result)
            self.assertIn('60.0', result)


class TestExecuteScriptPlugin(unittest.TestCase):
    def test_module_exports(self):
        import plugins.execute_script as p
        self.assertEqual(p.COMMAND_NAME, 'EXECUTE_SCRIPT')

    def test_missing_code(self):
        from plugins.execute_script import execute
        result = execute({})
        self.assertIn('code', result.lower())
        self.assertIn('no code', result.lower())

    def test_sandbox_execution(self):
        # Mock safe_subprocess to avoid actual code execution
        mock_result = MagicMock()
        mock_result.stdout = 'hello world'
        mock_result.stderr = ''
        from plugins.execute_script import WORKSPACE_DIR
        with patch('scripts.safe_subprocess.run_with_limits', return_value=mock_result):
            from plugins.execute_script import execute
            result = execute({'content': 'print("hello")', 'filename': 'test_script.py'})
            self.assertIn('hello world', result)

    def test_script_key_alias(self):
        mock_result = MagicMock()
        mock_result.stdout = 'script alias ok'
        mock_result.stderr = ''
        with patch('scripts.safe_subprocess.run_with_limits', return_value=mock_result):
            from plugins.execute_script import execute
            result = execute({'script': 'print("script alias")', 'filename': 'test_script.py'})
            self.assertIn('script alias ok', result)

    def test_code_key_alias(self):
        mock_result = MagicMock()
        mock_result.stdout = 'code alias ok'
        mock_result.stderr = ''
        with patch('scripts.safe_subprocess.run_with_limits', return_value=mock_result):
            from plugins.execute_script import execute
            result = execute({'code': 'print("code alias")', 'filename': 'test_script.py'})
            self.assertIn('code alias ok', result)

    def test_markdown_fence_strip(self):
        mock_result = MagicMock()
        mock_result.stdout = 'fence stripped'
        mock_result.stderr = ''
        with patch('scripts.safe_subprocess.run_with_limits', return_value=mock_result):
            from plugins.execute_script import execute
            result = execute({'script': '```python\nprint("hi")\n```', 'filename': 'test_script.py'})
            self.assertIn('fence stripped', result)

    def test_existing_file_resolution(self):
        mock_result = MagicMock()
        mock_result.stdout = 'existing file ok'
        mock_result.stderr = ''
        with patch('scripts.safe_subprocess.run_with_limits', return_value=mock_result):
            from plugins.execute_script import execute
            result = execute({'filename': 'plugins/system_status.py'})
            self.assertIn('existing file ok', result)
