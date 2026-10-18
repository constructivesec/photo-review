# ExifTool user configuration for Photo Review (loaded with -config).
# XPComment is stored by Windows as UTF-16LE. ExifTool's default definition uses UCS-2,
# which cannot represent characters outside the Basic Multilingual Plane (for example
# emoji), so XPComment is redefined here to use UTF-16 with surrogate pairs.
%Image::ExifTool::UserDefined = (
    'Image::ExifTool::Exif::Main' => {
        0x9c9c => {
            Name => 'XPComment',
            Format => 'undef',
            Writable => 'int8u',
            WriteGroup => 'IFD0',
            ValueConv => '$self->Decode($val,"UTF16","II")',
            ValueConvInv => '$self->Encode($val,"UTF16","II") . "\0\0"',
        },
    },
);
1;  # end
