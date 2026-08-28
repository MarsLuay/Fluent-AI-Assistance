import zipfile
import io

def test_zip():
    data = b'PK\x05\x06' + b'\x00' * 18
    zf = zipfile.ZipFile(io.BytesIO(data))
    print(getattr(zf, 'start_dir', 'no start_dir'))

if __name__ == '__main__':
    test_zip()
