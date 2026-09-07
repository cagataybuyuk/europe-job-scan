import unittest
from ejs.services.read_fence import ReadFenceReport

class ReadFenceTests(unittest.TestCase):
    def test_equal_fence_passes(self):
        r=ReadFenceReport({'total':52}, {'total':52})
        self.assertTrue(r.passed)
        self.assertEqual(r.differences,{})

    def test_changed_fence_fails(self):
        r=ReadFenceReport({'total':51}, {'total':52})
        self.assertFalse(r.passed)
        self.assertEqual(r.differences['total'], (51,52))

if __name__=='__main__':
    unittest.main()
