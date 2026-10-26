// espera: no vive lo suficiente
fn main() -> i32 {
    var y: i64 = 0;
    var r: &i64 = &y;
    {
        var x: i64 = 1;
        r = &x;
    }
    return *r as i32;
}
