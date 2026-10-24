import unittest
from unittest.mock import Mock, patch
import capture


class CaptureTests(unittest.TestCase):
    def test_conflicting_doi_pdf_requires_review_without_duplicate_save(self):
        router = Mock()
        with patch.object(capture, 'inspect', return_value=[{'parent':'ABCDEFGH','pdfs':[{'pages':13}], 'doi_conflict':True}]):
            result = capture.save_and_check(router, 'Verified title', 'https://research.ebsco.com/article')
        self.assertEqual(result['status'], 'pdf_identity_review_required')
        router.request.assert_not_called()

    def test_existing_pdf_skips_save(self):
        router = Mock()
        with patch.object(capture, 'inspect', return_value=[{'parent':'ABCDEFGH','pdfs':[{'pages':13}]}]):
            result = capture.save_and_check(router, 'Verified title', 'https://research.ebsco.com/article')
        self.assertFalse(result['saved'])
        router.request.assert_not_called()

    def test_one_save_then_verified_result(self):
        router = Mock()
        with patch.object(capture, 'inspect', side_effect=[[], [{'parent':'ABCDEFGH','pdfs':[{'pages':12}]}]]):
            result = capture.save_and_check(router, 'Verified title', 'https://research.ebsco.com/article')
        router.request.assert_called_once_with('save', 'https://research.ebsco.com/article')
        self.assertEqual(result['new_parent_keys'], ['ABCDEFGH'])

    def test_pending_does_not_repeat_save(self):
        router = Mock()
        with patch.object(capture, 'inspect', return_value=[]):
            result = capture.save_and_check(router, 'Verified title', 'https://research.ebsco.com/article', wait_seconds=0)
        self.assertEqual(result['status'], 'saved_pdf_pending_or_missing')
        router.request.assert_called_once()

    def test_unknown_save_outcome_does_not_replay(self):
        router = Mock()
        router.request.side_effect = RuntimeError('Unknown outcome')
        with patch.object(capture, 'inspect', return_value=[]):
            with self.assertRaises(RuntimeError):
                capture.save_and_check(router, 'Verified title', 'https://research.ebsco.com/article')
        router.request.assert_called_once()
