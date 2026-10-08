# kitty kitten: focus the matched window, raising its OS window with an
# xdg-activation token passed as the first argument.
from kittens.tui.handler import result_handler


def main(args):
    pass


@result_handler(no_ui=True)
def handle_result(args, answer, target_window_id, boss):
    token = args[1] if len(args) > 1 else ''
    w = boss.window_id_map.get(target_window_id)
    if w is None:
        return 'no such window'
    boss.set_active_window(w, switch_os_window_if_needed=True, activation_token=token)
    return f'focused {target_window_id} token={"yes" if token else "no"}'
