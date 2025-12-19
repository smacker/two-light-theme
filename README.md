# TwoLight

It is like [TwoDark](https://github.com/erremauro/TwoDark) but light version of it.

## How to add it to bat

```sh
cp TwoLight.tmTheme ~/.config/bat/themes/
bat cache --build
# Two Light should appear in the list
bat --list-themes
# Try it on some file
bat --theme=TwoLight some.file
```

Add to your `.bashrc` or `.zshrc`:

```sh
export BAT_THEME="TwoDark"
export BAT_THEME_DARK="TwoDark"
export BAT_THEME_LIGHT="TwoLight"
```

## Theme for delta

There is also theme for [delta](https://dandavison.github.io/delta/).

It requires `bat` theme to be installed first.
Then, include the following into `.gitconfig` (for macOs):

```sh
[include]
    path = /PATH/TO/delta/delta-two-theme.gitconfig

[core]
    pager = delta --features=$(defaults read -globalDomain AppleInterfaceStyle > /dev/null 2>&1 && echo two-dark || echo two-light)
[interactive]
    diffFilter = delta --color-only --features=$(defaults read -globalDomain AppleInterfaceStyle > /dev/null 2>&1 && echo two-dark || echo two-light)
```

## How it was made

- Take [TwoDark](https://github.com/erremauro/TwoDark) theme for Sublime Text (in TextMate format)
- Take [bat patch](https://github.com/sharkdp/bat/blob/9bf344f760e7644c2ecf6b6d2c9748b1c425611a/assets/patches/TwoDark.tmTheme.patch)
- Take [vim one](https://github.com/rakr/vim-one) theme that has both dark & light variants
- Ask AI to write a converter to generate light variant of TwoDark based on those inputs

## How to run the script

```sh
python3 convert_two_theme.py \
  --in sources/TwoDark.tmTheme \
  --vim sources/one.vim \
  --out TwoLight.tmTheme
```
